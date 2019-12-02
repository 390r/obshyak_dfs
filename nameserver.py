import json as _json
import random
import re
import time
from pathlib import Path
from datetime import datetime
from mongolock import MongoLock
import pymongo
from flask import Flask, flash, request, abort, jsonify
from flask_cors import CORS
from flask_socketio import SocketIO
from weakref import WeakValueDictionary
from threading import RLock

client = pymongo.MongoClient(
    'mongodb://heroku_8rbkrj6s:iccis5pen56ndm8r4cg1m3qsvm@ds227110.mlab.com:27110/heroku_8rbkrj6s?retryWrites=false')

db = client[
    'mongodb://heroku_8rbkrj6s:iccis5pen56ndm8r4cg1m3qsvm@ds227110.mlab.com:27110/heroku_8rbkrj6s'.split('/')[-1]]
ds = db.ds_egor

app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_handlers=True)


class DocumentLock(object):
    _lock = RLock()  # protects access to _locks dictionary
    _locks = WeakValueDictionary()

    def __init__(self):
        self.doc_id = 1

    def __enter__(self):
        with self._lock:
            self.lock = self._locks.get(self.doc_id)
            if self.lock is None:
                self._locks[self.doc_id] = self.lock = RLock()
        self.lock.acquire()

    def __exit__(self, exc_type, exc_value, traceback):
        self.lock.release()
        self.lock = None  # make available for garbage collection


class NameServer:

    # PROCESS FILES

    @staticmethod
    def get_fileserver_to_retrieve(filepath):
        filepath_split = split_filepath(filepath)
        ds_find = ds.file_system.find_one(
            {'ancestors': filepath_split[:-1], 'name': filepath_split[-1], 'type': 'file'}, {'_id': 0})
        if ds_find is not None:
            return random.choice(ds_find['servers'])

    @staticmethod
    def get_fileserver_to_upload(filepath):
        filepath_split = split_filepath(filepath)
        exists = ds.file_system.find({'ancestors': filepath_split[:-1], 'name': filepath_split[-1]}).count() > 0
        random_fileserver = list(ds.servers.aggregate([{'$sample': {'size': 1}}, {'$project': {'_id': 0}}]))
        return exists, random_fileserver

    # INTERNAL

    def list_servers(self, node_ip, node_port, node_sid):
        return list(ds.servers.find({'$or': [{'server_ip': {'$ne': node_ip}},
                                             {'server_port': {'$ne': node_port}}]}, {'_id': 0}))

    @staticmethod
    def init_fileserver(node_socket_id, server_ip, server_port, server_files):
        server_files_lists = {}
        to_be_deleted = []
        to_be_downloaded = []

        ds.servers.remove({'server_ip': server_ip, 'server_port': server_port})
        removed_old = ds.file_system.update_many({'servers': {'$elemMatch': {'server_ip': server_ip}}},
                                                 {'$pull': {'servers': {'server_ip': server_ip,
                                                                        'server_port': server_port}}})
        print(server_files)

        for filepath, timestamp in server_files.items():
            server_files_lists[filepath] = (split_filepath(filepath), timestamp)

        for filepath, (filepath_list, timestamp) in server_files_lists.items():
            file_exists = ds.file_system.find_one(
                {'type': 'file', 'ancestors': filepath_list[:-1], 'name': filepath_list[-1]}) is not None
            file_timestamps_good = ds.file_system.find(
                {'type': 'file', 'ancestors': filepath_list[:-1], 'name': filepath_list[-1],
                 'timestamp': timestamp}).count()
            if file_exists:
                if file_timestamps_good:
                    ds.file_system.update({'type': 'file',
                                           'ancestors': filepath_list[:-1],
                                           'name': filepath_list[-1]},
                                          {'$push': {'servers': {'node_socket_id': node_socket_id,
                                                                 'server_ip': server_ip,
                                                                 'server_port': server_port}}},
                                          upsert=True)
                else:
                    to_be_deleted.append(filepath)
            else:
                to_be_deleted.append(filepath)
        to_be_downloaded = list(ds.file_system.find({'type': 'file', '$or': [{
            '$and': [{'ancestors': {'$nin': [filepath_list[0][:-1] for filepath_list in server_files_lists.values()]}},
                     {'name': {'$nin': [filepath_list[0][-1] for filepath_list in server_files_lists.values()]}}]},
            {
                'name': {'$nin': [filepath_list[0][-1] for filepath_list in server_files_lists.values()]}
            }]}, {'_id': 0}))

        ds.servers.insert({'node_socket_id': node_socket_id,
                           'server_ip': server_ip,
                           'server_port': server_port})
        return True, removed_old.matched_count > 0, to_be_deleted, to_be_downloaded

    def drop_node(self, sid):
        ds.servers.remove({'node_socket_id': sid})
        ds.file_system.update_many({'servers': {'$elemMatch': {'node_socket_id': sid}}},
                                   {'$pull': {'servers': {'node_socket_id': sid}}})
        if ds.file_system.find({'type': 'file', 'servers': {'$size': 0}}).count():
            ds.file_system.remove({'type': 'file', 'servers': {'$size': 0}})

    @staticmethod
    def allocate_fle(node_socket_id, server_ip, server_port, filepath, timestamp, replicated):
        filepath_split = split_filepath(filepath)
        print('node_socket_id:', node_socket_id)
        with DocumentLock():
            if not replicated:
                socketio.emit('delete_file', {'filepath': filepath}, broadcast=True, namespace='/',
                              skip_sid=node_socket_id)
                ds.file_system.remove({'ancestors': filepath_split[:-1],
                                       'ancestors_str': '/'.join(filepath_split[:-1]),
                                       'name': filepath_split[-1]})
                ds.file_system.insert({'ancestors': filepath_split[:-1],
                                       'ancestors_str': '/'.join(filepath_split[:-1]),
                                       'name': filepath_split[-1],
                                       'type': 'file',
                                       'timestamp': timestamp,
                                       'servers': [{'node_socket_id': node_socket_id,
                                                    'server_ip': server_ip,
                                                    'server_port': server_port}]})
            else:
                ds.file_system.update({'ancestors': filepath_split[:-1],
                                       'name': filepath_split[-1]},
                                      {'$push': {'servers': {'node_socket_id': node_socket_id,
                                                             'server_ip': server_ip,
                                                             'server_port': server_port}}},
                                      upsert=True)

    # LS, CD, MKDIR, RM

    def list_dir(self, path):
        path_split = split_filepath(path)
        return list(ds.file_system.find({'ancestors': path_split}, {'servers': 0, '_id': 0}))

    def open_dir(self, path):
        path_split = split_filepath(path)
        if len(path_split) > 0:
            return ds.file_system.find({'ancestors': path_split[:-1],
                                        'name': path_split[-1],
                                        'type': 'dir'}).count()
        else:
            return ds.file_system.find({'ancestors': [],
                                        'type': 'dir'}).count()

    def make_dir(self, path):
        path_split = split_filepath(path)
        if ds.file_system.find({'ancestors': path_split[:-1],
                                'name': path_split[-1],
                                'type': 'dir'}).count() > 0:
            return False, "Such dir already exists"
        else:
            ds.file_system.insert({'ancestors': path_split[:-1],
                                   'name': path_split[-1],
                                   'type': 'dir',
                                   'servers': []})
            return True, None

    def delete_file(self, path, node_ip, node_port):
        path_split = split_filepath(path)
        ds.file_system.update_many({'ancestors': path_split[:-1],
                                    'name': path_split[-1],
                                    'servers': {'$elemMatch': {'server_ip': node_ip,
                                                               'server_port': node_port}}},
                                   {'$pull': {'servers': {'server_ip': node_ip,
                                                          'server_port': node_port}}})
        if ds.file_system.find({'ancestors': path_split[:-1],
                                'name': path_split[-1],
                                'servers': {'$size': 0}}).count():
            ds.file_system.remove({'ancestors': path_split[:-1],
                                   'name': path_split[-1]})

        return True, None

    def delete_dir(self, path, node_ip, node_port):
        path_split = split_filepath(path)
        ds.file_system.update_many(
            {'$or': [{'ancestors_str': re.compile('^{}'.format('/'.join(path_split)), re.IGNORECASE)},
                     {'ancestors': path_split[:-1], 'name': path_split[-1]}],
             'servers': {'$elemMatch': {'server_ip': node_ip,
                                        'server_port': node_port}}},
            {'$pull': {'servers': {'server_ip': node_ip,
                                   'server_port': node_port}}})
        print({'ancestors_str': re.compile('^{}'.format('/'.join(path_split)), re.IGNORECASE)})
        records = ds.file_system.find({'$or': [
            {'ancestors_str': re.compile('^{}'.format('/'.join(path_split)), re.IGNORECASE), 'servers': {'$size': 0}},
            {'ancestors_str': '/'.join(path_split), 'servers': {'$exists': False}},
            {'ancestors': path_split[:-1], 'name': path_split[-1], 'servers': {'$size': 0}}
        ]})
        print('records.count:', records.count())
        for r in records:
            ds.file_system.remove(r)
        return True, None

    # CP, MV

    def copy_file(self, path_from, path_to):
        path_from_split = split_filepath(path_from)
        path_to_split = split_filepath(path_to)
        if ds.file_system.find({'type': 'file',
                                'ancestors': path_from_split[:-1],
                                'name': path_from_split[-1]}).count():
            if not ds.file_system.find({'type': 'file',
                                        'ancestors': path_to_split[:-1],
                                        'name': path_to_split[-1]}).count():
                socketio.emit('copy_file', {'path_from': path_from, 'path_to': path_to}, broadcast=True, namespace='/')
                return True, None
            else:
                return False, "Can't copy to this path. File already exists."
        else:
            return False, "No such file found in the File System."

    def file_copied_ack(self, path_from, path_to, node_socket_id, node_ip, node_port):
        path_to_split = split_filepath(path_to)
        ds_find = ds.file_system.find({'ancestors': path_to_split[:-1], 'name': path_to_split[-1]})
        with DocumentLock():
            if ds_find.count():
                ds.file_system.update_many({'ancestors': path_to_split[:-1],
                                            'name': path_to_split[-1],
                                            'type': 'file'},
                                           {'$push': {'servers': {'server_ip': node_ip,
                                                                  'server_port': node_port}}})
            else:
                ds.file_system.insert({'ancestors': path_to_split[:-1],
                                       'ancestors_str': '/'.join(path_to_split[:-1]),
                                       'name': path_to_split[-1],
                                       'type': 'file',
                                       'timestamp': int(time.time()),
                                       'servers': [{'node_socket_id': node_socket_id,
                                                    'server_ip': node_ip,
                                                    'server_port': node_port}]})

    def move_file(self, path_from, path_to):
        path_from_split = split_filepath(path_from)
        path_to_split = split_filepath(path_to)
        if ds.file_system.find({'type': 'file',
                                'ancestors': path_from_split[:-1],
                                'name': path_from_split[-1]}).count():
            if not ds.file_system.find({'type': 'file',
                                        'ancestors': path_to_split[:-1],
                                        'name': path_to_split[-1]}).count():
                socketio.emit('move_file', {'path_from': path_from, 'path_to': path_to}, broadcast=True, namespace='/')
                return True, None
            else:
                return False, "Can't move to this path. File already exists."
        else:
            return False, "No such file found in the File System."

    def file_moved_ack(self, path_from, path_to, node_socket_id, node_ip, node_port):
        path_from_split = split_filepath(path_from)
        path_to_split = split_filepath(path_to)
        with DocumentLock():
            ds.file_system.update_many({'ancestors': path_from_split[:-1],
                                        'name': path_from_split[-1]},
                                       {'$pull': {'servers': {'server_ip': node_ip,
                                                              'server_port': node_port}}})
            ds.file_system.remove({'$or': [{'ancestors': path_from_split[:-1],
                                            'name': path_from_split[-1],
                                            'servers': {'$exists': False}},
                                           {'ancestors': path_from_split[:-1],
                                            'name': path_from_split[-1],
                                            'servers': {'$size': 0}}
                                           ]})
            ds_find = ds.file_system.find({'ancestors': path_to_split[:-1], 'name': path_to_split[-1]})
            if ds_find.count():
                ds.file_system.update_many({'ancestors': path_to_split[:-1],
                                            'name': path_to_split[-1]},
                                           {'$push': {'servers': {'server_ip': node_ip,
                                                                  'server_port': node_port}}})
            else:
                ds.file_system.insert({'ancestors': path_to_split[:-1],
                                       'ancestors_str': '/'.join(path_to_split[:-1]),
                                       'name': path_to_split[-1],
                                       'type': 'file',
                                       'timestamp': int(time.time()),
                                       'servers': [{'node_socket_id': node_socket_id,
                                                    'server_ip': node_ip,
                                                    'server_port': node_port}]})

    def is_dir(self, path):
        path_split = split_filepath(path)
        return ds.file_system.find({'type': 'dir', 'ancestors': path_split[:-1], 'name': path_split[-1]}).count() > 0


nameserver = NameServer()


# PROCESS FILES

@app.route('/get-file/', methods=['GET'])
def get_file():
    filepath = request.args.get('filepath')
    if filepath is None:
        flash('No filepath')
        return abort(412, {'message': 'No filepath was specified'})
    fileserver = nameserver.get_fileserver_to_retrieve(filepath)
    if fileserver is not None:
        return _json.dumps({'retrieved': fileserver})
    else:
        return abort(404, {'message': 'No such file stored'})


@app.route('/upload-file/', methods=['POST'])
def get_server_to_upload_file():
    if request.method == 'POST':
        filepath = request.form.get('filepath')
        exists, fileserver = nameserver.get_fileserver_to_upload(filepath)
        return _json.dumps({'exists': exists, 'fileserver': fileserver, 'uri': '/upload-file/'})


# LS, CD, MKDIR, RM

@app.route('/list-dir/', methods=['GET'])
def list_dir():
    path = request.args.get('path')
    return _json.dumps({'retrieved': nameserver.list_dir(path)})


@app.route('/open-dir/', methods=['GET'])
def open_dir():
    path = request.args.get('path')
    allowed = nameserver.open_dir(path)
    if allowed:
        return jsonify(success=True)
    else:
        return jsonify({"message": allowed}), 404


@app.route('/make-dir/', methods=['POST'])
def make_dir():
    path = request.form['path']
    created, err = nameserver.make_dir(path)
    if created:
        return jsonify(success=True)
    else:
        return jsonify({"message": err}), 400


@app.route('/delete-file/', methods=['POST'])
def delete_file():
    path = request.form['path']
    path_split = split_filepath(path)
    if nameserver.is_dir(path):
        socketio.emit('delete_dir', {'filepath': path + '/'}, broadcast=True, namespace='/')
    else:
        socketio.emit('delete_file', {'filepath': path}, broadcast=True, namespace='/')

    if ds.file_system.find(
            {'ancestors': path_split[:-1], 'name': path_split[-1], 'servers': {'$size': 0}}).count():
        ds.file_system.remove({'ancestors': path_split[:-1], 'name': path_split[-1]})
    return jsonify(success=True)


# CP, MV

@app.route('/copy-file/', methods=['POST'])
def copy_file():
    path_from = request.form['path_from']
    path_to = request.form['path_to']
    copied, err = nameserver.copy_file(path_from, path_to)
    if copied:
        return _json.dumps({})
    else:
        return jsonify({"message": err}), 400, {'ContentType': 'application/json'}


@app.route('/move-file/', methods=['POST'])
def move_file():
    path_from = request.form['path_from']
    path_to = request.form['path_to']
    copied, err = nameserver.move_file(path_from, path_to)
    if copied:
        return _json.dumps({})
    else:
        return jsonify({"message": err}), 400, {'ContentType': 'application/json'}


# INTERNAL

@socketio.on('connect', namespace='/test')
def test_connect():
    socketio.emit('my response', {'data': 'Connected'})


@socketio.on('disconnect')
def disconnect():
    currentSocketId = request.sid
    nameserver.drop_node(request.sid)
    print('disconnected:', currentSocketId)


@socketio.on('init_fileserver')
def init_fileserver(json):
    node_socket_id = request.sid
    print('connected:', node_socket_id)
    server_ip = json['server_ip']
    server_port = json['server_port']
    server_files = json['server_files']
    allocated, rewritten, to_be_deleted, to_be_downloaded = nameserver.init_fileserver(node_socket_id, server_ip,
                                                                                       server_port, server_files)
    return _json.dumps({'allocated': allocated,
                        'rewritten': rewritten,
                        'to_be_deleted': to_be_deleted,
                        'to_be_downloaded': to_be_downloaded})


@socketio.on('list_servers')
def list_servers(json):
    node_socket_id = request.sid
    server_ip = json['server_ip']
    server_port = json['server_port']
    res = nameserver.list_servers(server_ip, server_port, node_socket_id)
    return _json.dumps(res)


@socketio.on('deleted_file_ack')
def deleted_file_ack(json_data):
    print(json_data)
    node_socket_id = request.sid
    path = json_data['path']
    node_ip = json_data['server_ip']
    node_port = json_data['server_port']
    print('delete_file ack:', node_ip, node_port, node_socket_id)
    nameserver.delete_file(path, node_ip, node_port)


@socketio.on('deleted_dir_ack')
def deleted_dir_ack(json_data):
    print(json_data)
    node_socket_id = request.sid
    path = json_data['path']
    node_ip = json_data['server_ip']
    node_port = json_data['server_port']
    print('delete_dir ack:', node_ip, node_port, node_socket_id)
    nameserver.delete_dir(path, node_ip, node_port)


@socketio.on('file_copied_ack')
def file_copied_ack(json_data):
    node_socket_id = request.sid
    path_from = json_data['path_from']
    path_to = json_data['path_to']
    node_ip = json_data['server_ip']
    node_port = json_data['server_port']
    print('file_copied_ack:', node_ip, node_port, node_socket_id)
    nameserver.file_copied_ack(path_from, path_to, node_socket_id, node_ip, node_port)


@socketio.on('file_moved_ack')
def file_moved_ack(json_data):
    node_socket_id = request.sid
    path_from = json_data['path_from']
    path_to = json_data['path_to']
    node_ip = json_data['server_ip']
    node_port = json_data['server_port']
    print('file_moved_ack:', node_ip, node_port, node_socket_id)
    nameserver.file_moved_ack(path_from, path_to, node_socket_id, node_ip, node_port)


@socketio.on('allocate_file')
def allocate_file(json_data):
    node_socket_id = request.sid
    filepath = json_data['filepath']
    server_ip = json_data['server_ip']
    server_port = json_data['server_port']
    replicated = json_data['replicated']
    timestamp = json_data.get('timestamp')
    nameserver.allocate_fle(node_socket_id, server_ip, server_port, filepath, timestamp, replicated)
    return True


def main():
    app.secret_key = 'MaxGay'
    socketio.run(app, debug=True, port=5000, host='127.0.0.1')
    app.run(host='127.0.0.1', port=5000, debug=True)


def split_filepath(filepath):
    filepath_list = list(Path(filepath).parts)
    return filepath_list[1:] if filepath_list[0] == '/' else filepath_list


if __name__ == '__main__':
    main()
