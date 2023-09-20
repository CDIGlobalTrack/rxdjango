from django.db import connection, connections

SQLS = {
    'postgresql': 'SELECT txid_current();',
    'mysql': 'SELECT @@GLOBAL.TRX_ID;',
}

def get_transaction_id(using=None):
    if using:
        conn = connections[using]
    else:
        conn = connection

    if not conn.in_atomic_block:
        return None

    with conn.cursor() as cursor:
        try:
            cursor.execute(SQLS[conn.vendor])
        except KeyError:
            return None

        return cursor.fetchone()[0]
