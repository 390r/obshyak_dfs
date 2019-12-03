import time

import pymongo
import os
import requests
import re

client = pymongo.MongoClient(
    'mongodb://heroku_8rbkrj6s:iccis5pen56ndm8r4cg1m3qsvm@ds227110.mlab.com:27110/heroku_8rbkrj6s?retryWrites=false')

db = client[
    'mongodb://heroku_8rbkrj6s:iccis5pen56ndm8r4cg1m3qsvm@ds227110.mlab.com:27110/heroku_8rbkrj6s'.split('/')[-1]]
ds = db.ds


def allocate_server():
    pass


if __name__ == '__main__':
    # ds.drop()
    # ds.servers.drop()
    # ds.file_system.drop()

    ip = requests.get('https://api.ipify.org').text
    print('My public IP address is:', ip)


    # print(os.path.getmtime('/Users/egorbak/PycharmProjects/PlayGround/ds/project/storage/rootDir2/2.png'))

    start = time.time()
    print("Servers:")
    for i in list(ds.servers.find({}, {'_id': 0})):
        print(i)

    print("File System:")
    for i in list(ds.file_system.find({}, {'_id': 0})):
        print(i)
    print("Time:")
    print(time.time() - start)

    print(list(ds.file_system.find({'ancestors': [], 'name': 'HELLO1.txt', 'servers': {'$size': 0}})))


    test = ['/']
    print(test[:-1])
