# Copyright 2016 Red Hat, Inc.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import sqlite3


class Cache(object):

    def __init__(self):
        self._getconn()

        self.conn.execute('''CREATE TABLE IF NOT EXISTS cache
                          (id TEXT PRIMARY KEY     NOT NULL,
                           data            TEXT    NOT NULL);''')
        self.conn.close()

    def _getconn(self):
        self.conn = sqlite3.connect('/var/run/nova/test.db')

    def add(self, id, data):
        self._getconn()
        s = ("INSERT INTO cache (id, data) VALUES (\'{id}\', \'{data}\')"
             .format(id=id, data=data))
        self.conn.execute(s)
        self.conn.commit()
        self.conn.close()

    def get(self, id):
        data = None
        self._getconn()
        cursor = self.conn.execute("SELECT id, data from cache where "
                                   "id=\'%s\'" % id)
        for row in cursor:
            data = row[1]
        self.conn.close()
        return data
