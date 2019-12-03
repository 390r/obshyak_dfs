import json
import os
import shutil
import requests
import random
import time
import socketio
from flask import Flask, request, jsonify, send_file, abort
from flask_cors import CORS

app = Flask(__name__)
CORS(app)
sio = socketio.Client()


class FileServer:
    config = None
    nameserver_addr = None
    node_info = None
    client = None

    def init_callback(self, val):
        print('init_callback:', val)

    def init_fn(self, config):
        global sio
        ip = requests.get('https://api.ipify.org').text
        self.config = config
        self.nameserver_addr = 'http://{}:{}'.format(config['nameserver']['ip'], config['nameserver']['port'])
        self.node_info = {'server_ip': ip, 'server_port': config['port']}
        available_files = self.get_available_files(config['rootDir'])
        sio.connect(self.nameserver_addr)
        print('my sid is', sio.sid)
        sio.emit('init_fileserver', {
            **self.node_info,
            'server_files': available_files
        }, callback=lambda payload: self.replicate_on_init(payload))

        app.secret_key = 'MaxGay'
        app.run(host='0.0.0.0', port=config['port'])

    def replicate_on_init(self, payload):
        payload = json.loads(payload)
        to_be_deleted = payload['to_be_deleted']
        to_be_downloaded = payload['to_be_downloaded']
        print(payload)
        for path in to_be_deleted:
            self.delete_file(path)
        for i in to_be_downloaded:
            if len(i['ancestors']) > 0:
                filepath = '/{}/{}'.format(i['ancestors_str'], i['name'])
            else:
                filepath = '/{}'.format(i['name'])
            server = random.choice(i['servers'])
            res = requests.get('http://{}:{}/download-file/'.format(server['server_ip'], server['server_port']),
                               data={'filepath': filepath})
            self.write_file(res.content, filepath, i['timestamp'], filel_id_bytes=True)

    @staticmethod
    def get_available_files(root_dir):
        file_paths = {}  # List which will store all of the full filepaths.

        # Walk the tree.
        for root_tmp, directories, files in os.walk(root_dir):
            for filename in files:
                # Join the two strings in order to form the full filepath.
                root = root_tmp[len(root_dir):]
                filepath = os.path.join(root, filename)
                file_paths[filepath] = int(os.path.getmtime(root_dir + filepath))  # Add it to the list.

        return file_paths

    def replicate_to_servers(self, servers, local_filepath, filepath, timestamp):
        servers = json.loads(servers)
        for serv in servers:
            print(filepath)
            res = requests.post(url='http://{}:{}/upload-file/'.format(serv['server_ip'], serv['server_port']),
                                files={'file': open(local_filepath, 'rb')},
                                data={'filepath': filepath, 'replicated': True, 'timestamp': timestamp})

    def write_file(self, file, filepath, timestamp, filel_id_bytes=False):
        basename = self.config['rootDir'] + os.path.split(filepath)[0]
        path = self.config['rootDir'] + filepath
        if not os.path.exists(basename):
            os.makedirs(basename)
        if filel_id_bytes:
            with open(path, 'wb') as f:
                f.write(file)
        else:
            file.save(path)
        size = os.path.getsize(path)
        os.utime(path, (timestamp, timestamp))
        self.acknowledge_nameserver(path, filepath, timestamp, size, replicated=False)

    def write_replica(self, file, filepath, timestamp):
        basename = self.config['rootDir'] + os.path.split(filepath)[0]
        path = self.config['rootDir'] + filepath
        if not os.path.exists(basename):
            os.makedirs(basename)
        file.save(path)
        print('(timestamp, timestamp)', (timestamp, timestamp))
        os.utime(path, (timestamp, timestamp))
        self.acknowledge_nameserver(path, filepath, timestamp, replicated=True)

    def acknowledge_nameserver(self, path, filepath, timestamp, size=None, replicated=False):
        sio.emit('allocate_file',
                 {'filepath': filepath,
                  'timestamp': timestamp,
                  'size': size,
                  'replicated': replicated,
                  **self.node_info},
                 callback=lambda y: sio.emit('list_servers', {**self.node_info},
                                             callback=lambda x: self.replicate_to_servers(x, path, filepath,
                                                                                          timestamp)) if not replicated else print(
                     None))

    def get_file(self, filepath):
        basename = os.path.split(filepath)[0]
        path = self.config['rootDir'] + filepath
        if not os.path.exists(path):
            return False, {'msg': 'File does not exists', 'code': 400}
        else:
            return True, send_file(path, as_attachment=True)

    def delete_file(self, path):
        """
        Statuses:
            200 - OK
            404 - Does not exists
        :param path:
        :return:
        """
        full_path = self.config['rootDir'] + path
        print('Deleted:', full_path)
        if not os.path.exists(full_path):
            return False, 404
        os.remove(full_path)
        return True, 200

    def delete_dir(self, path):
        """
        Statuses:
            200 - OK
            404 - Does not exists
        :param path:
        :return:
        """
        full_path = self.config['rootDir'] + path
        print('Deleted:', full_path)
        if not os.path.exists(full_path):
            return False, 404
        shutil.rmtree(full_path, ignore_errors=True)
        return True, 200

    def copy_file(self, path_from, path_to):
        """
        Statuses:
            200 - OK
            404 - Does not exists
        :param path:
        :return:
        """
        full_path_from = self.config['rootDir'] + path_from
        full_path_to = self.config['rootDir'] + path_to
        if not os.path.exists(full_path_from):
            return False, 404
        shutil.copyfile(full_path_from, full_path_to)
        return True, 200

    def move_file(self, path_from, path_to):
        """
        Statuses:
            200 - OK
            404 - Does not exists
        :param path:
        :return:
        """
        full_path_from = self.config['rootDir'] + path_from
        full_path_to = self.config['rootDir'] + path_to
        if not os.path.exists(full_path_from):
            return False, 404
        shutil.move(full_path_from, full_path_to)
        return True, 200


fileserver = FileServer()


@sio.on('delete_file')
def delete_file(json_data):
    path = json_data['filepath']
    deleted, status = fileserver.delete_file(path)
    if deleted:
        sio.emit('deleted_file_ack', {'path': path, **fileserver.node_info})
        return True
    return False


@sio.on('delete_dir')
def delete_dir(json_data):
    path = json_data['filepath']
    deleted, status = fileserver.delete_dir(path)
    if deleted:
        sio.emit('deleted_dir_ack', {'path': path, **fileserver.node_info})
        return True
    return False


@sio.on('copy_file')
def copy_file(json_data):
    path_from = json_data['path_from']
    path_to = json_data['path_to']
    copied, status = fileserver.copy_file(path_from, path_to)
    if copied:
        sio.emit('file_copied_ack', {'path_from': path_from, 'path_to': path_to, **fileserver.node_info})
        return True
    return False


@sio.on('move_file')
def move_file(json_data):
    path_from = json_data['path_from']
    path_to = json_data['path_to']
    copied, status = fileserver.move_file(path_from, path_to)
    if copied:
        sio.emit('file_moved_ack', {'path_from': path_from, 'path_to': path_to, **fileserver.node_info})
        return True
    return False


@app.route('/upload-file/', methods=['POST'])
def write_file():
    file = request.files['file']
    path = request.form['filepath']
    replicated = request.form.get('replicated')
    print('replicated:', replicated)
    if replicated is None:
        timestamp = int(time.time())
        print('timestamp', timestamp)
        fileserver.write_file(file, path, timestamp)
    else:
        timestamp = int(request.form.get('timestamp'))
        fileserver.write_replica(file, path, timestamp)
    return jsonify(success=True)


@app.route('/download-file/', methods=['GET'])
def download_file():
    filepath = request.args['filepath']
    succ, data = fileserver.get_file(filepath)
    if succ:
        return data
    return abort(data['code'], data)


def main():
    config = None
    with open('datanode_1.json', 'r') as f:
        config = json.loads(f.read())

    if config is None:
        exit(1)

    fileserver.init_fn(config)


if __name__ == '__main__':
    main()
