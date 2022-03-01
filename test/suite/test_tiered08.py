#!/usr/bin/env python
#
# Public Domain 2014-present MongoDB, Inc.
# Public Domain 2008-2014 WiredTiger, Inc.
#
# This is free and unencumbered software released into the public domain.
#
# Anyone is free to copy, modify, publish, use, compile, sell, or
# distribute this software, either in source code form or as a compiled
# binary, for any purpose, commercial or non-commercial, and by any
# means.
#
# In jurisdictions that recognize copyright laws, the author or authors
# of this software dedicate any and all copyright interest in the
# software to the public domain. We make this dedication for the benefit
# of the public at large and to the detriment of our heirs and
# successors. We intend this dedication to be an overt act of
# relinquishment in perpetuity of all present and future rights to this
# software under copyright law.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
# IN NO EVENT SHALL THE AUTHORS BE LIABLE FOR ANY CLAIM, DAMAGES OR
# OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE,
# ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
# OTHER DEALINGS IN THE SOFTWARE.
#
# [TEST_TAGS]
# tiered_storage:checkpoint
# tiered_storage:flush_tier
# [END_TAGS]
#
from helper_tiered import get_auth_token, get_bucket1_name, get_bucket1_region
from helper_tiered import generate_s3_prefix
from wtscenario import make_scenarios
import os, threading, time, wttest
from wiredtiger import stat
from wtthread import checkpoint_thread, flush_tier_thread

# test_tiered08.py
#   Run background checkpoints and flush_tier operations while inserting
#   data into a table from another thread.
class test_tiered08(wttest.WiredTigerTestCase):
    storage_sources = [
        ('local', dict(ss_name = 'local_store',
            auth_token = get_auth_token('local_store'),
            bucket = get_bucket1_name('local_store'),
            bucket_region = get_bucket1_region('local_store'),
            bucket_prefix = "pfx_")),
        ('s3', dict(ss_name = 's3_store',
            auth_token = get_auth_token('s3_store'),
            bucket = get_bucket1_name('s3_store'),
            bucket_region = get_bucket1_region('s3_store'),
            bucket_prefix = generate_s3_prefix())),
    ]
    # Make scenarios for different cloud service providers
    scenarios = make_scenarios(storage_sources)

    batch_size = 100000

    # Keep inserting keys until we've done this many flush and checkpoint ops.
    ckpt_flush_target = 10

    uri = "table:test_tiered08"

    def conn_config(self):
        if self.ss_name == 'local_store' and not os.path.exists(self.bucket):
            os.mkdir(self.bucket)
        return \
          'statistics=(fast),' + \
          'tiered_storage=(auth_token=%s,' % self.auth_token + \
          'bucket=%s,' % self.bucket + \
          'bucket_region=%s,' % self.bucket_region + \
          'bucket_prefix=%s,' % self.bucket_prefix + \
          'name=%s),tiered_manager=(wait=0)' % self.ss_name

    # Load the storage store extension.
    def conn_extensions(self, extlist):
        config = ''
        # S3 store is built as an optional loadable extension, not all test environments build S3.
        if self.ss_name == 's3_store':
            #config = '=(config=\"(verbose=1)\")'
            extlist.skip_if_missing = True
        # Windows doesn't support dynamically loaded extension libraries.
        if os.name == 'nt':
            extlist.skip_if_missing = True

        extlist.extension('storage_sources', self.ss_name + config)

    def get_stat(self, stat):
        stat_cursor = self.session.open_cursor('statistics:')
        val = stat_cursor[stat][2]
        stat_cursor.close()
        return val

    def key_gen(self, i):
        return 'KEY' + str(i)

    def value_gen(self, i):
        return 'VALUE_' + 'filler' * (i % 12) + str(i)

    # Populate the test table.  Keep adding keys until the desired number of flush and
    # checkpoint operations have happened.
    def populate(self):
        ckpt_count = 0
        flush_count = 0
        nkeys = 0

        self.pr('Populating tiered table')
        c = self.session.open_cursor(self.uri, None, None)
        while ckpt_count < self.ckpt_flush_target or flush_count < self.ckpt_flush_target:
            for i in range(nkeys, nkeys + self.batch_size):
                c[self.key_gen(i)] = self.value_gen(i)
            nkeys += self.batch_size
            ckpt_count = self.get_stat(stat.conn.txn_checkpoint)
            flush_count = self.get_stat(stat.conn.flush_tier)
        c.close()
        return nkeys

    def verify(self, key_count):
        self.pr('Verifying tiered table')
        c = self.session.open_cursor(self.uri, None, None)
        for i in range(key_count):
            self.assertEqual(c[self.key_gen(i)], self.value_gen(i))
        c.close()

    def test_tiered08(self):

        # FIXME-WT-7833
        #     This test can trigger races in file handle access during flush_tier.
        #     We will re-enable it when that is fixed.
        self.skipTest('Concurrent flush_tier and insert operations not supported yet.')

        cfg = self.conn_config()
        self.pr('Config is: ' + cfg)
        intl_page = 'internal_page_max=16K'
        base_create = 'key_format=S,value_format=S,' + intl_page
        self.session.create(self.uri, base_create)

        done = threading.Event()
        ckpt = checkpoint_thread(self.conn, done)
        flush = flush_tier_thread(self.conn, done)

        # Start background threads and give them a chance to start.
        ckpt.start()
        flush.start()
        time.sleep(0.5)

        key_count = self.populate()

        done.set()
        flush.join()
        ckpt.join()

        self.verify(key_count)

        self.close_conn()
        self.pr('Reopening tiered table')
        self.reopen_conn()

        self.verify(key_count)

if __name__ == '__main__':
    wttest.run()
