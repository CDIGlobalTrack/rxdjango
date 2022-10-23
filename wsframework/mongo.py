import json
import sys
import pymongo
import gridfs
from bson.objectid import ObjectId
from django.conf import settings
from django.utils import timezone

def get_db():
    client = pymongo.MongoClient(settings.MONGO_URL)
    return client[settings.MONGO_TRANSACTIONS_DB]

def init_collections(channel_type):
    db = get_db()
    transactions = db[channel_type]
    latest = db[f'{channel_type}-latest']
    log = db[f'{channel_type}-log']

    drop_collections(channel_type)

    # Used for upsert
    transactions.create_index(
        [
            ['channel_instance_id', pymongo.ASCENDING],
            ['instance_type', pymongo.ASCENDING],
            ['instance_id', pymongo.ASCENDING],
        ],
        name='instance',
        unique=True,
    )

    # Used to fetch new transactions
    transactions.create_index(
        [
            ['channel_instance_id', pymongo.ASCENDING],
            ['transaction', pymongo.DESCENDING],
        ],
        name='transaction',
    )

    # A log of all transactions, used to debug
    transactions.create_index(
        [
            ['channel_instance_id', pymongo.ASCENDING],
            ['transaction', pymongo.DESCENDING],
        ],
        name='transaction',
    )

    # Fetch latest transaction for a channel instance
    latest.create_index('channel_instance_id')

def drop_collections(channel_type):
    db = get_db()
    db[channel_type].drop()
    db[f'{channel_type}-latest'].drop()
    db[f'{channel_type}-log'].drop()


def get_latest_transaction_id(channel_type, channel_instance_id):
    db = get_db()
    latest = db[f'{channel_type}-latest']

    try:
        result = latest.find_one({ 'channel_instance_id': channel_instance_id })
        return result['transaction']
    except TypeError:
        # Start with 1, so that first transaction is different
        # from default client behavior
        return 1

def log_transaction(channel_type, channel_instance_id, payload):
    db = get_db()
    fs = gridfs.GridFS(db)

    transactions = db[channel_type]
    latest = db[f'{channel_type}-latest']

    for update in payload['payload']:
        update['channel_instance_id'] = channel_instance_id
        update['transaction'] = payload['transaction']

        try:
            transactions.replace_one(
                {
                    'channel_instance_id': channel_instance_id,
                    'instance_type': update['instance_type'],
                    'instance_id': update['instance_id'],
                },
                update,
                upsert=True,
            )
        except pymongo.errors.DocumentTooLarge:
            data = json.dumps(update['data']).encode()
            ref = fs.put(data)
            update['data'] = ref

            transactions.replace_one(
                {
                    'channel_instance_id': channel_instance_id,
                    'instance_type': update['instance_type'],
                    'instance_id': update['instance_id'],
                },
                update,
                upsert=True,
            )



        if settings.TRANSACTION_LOG_ENABLED:
            # This is for debugging, and it'll drop documents too large
            log = db[f'{channel_type}-log']
            log.insert_one(update)

    latest.replace_one(
        {
            'channel_instance_id': channel_instance_id,
        },
        {
            'channel_instance_id': channel_instance_id,
            'transaction': payload['transaction'],
            'tstamp': timezone.now(),
        },
        upsert=True,
    )


def _unpack(item):
    del item['_id']

    if isinstance(item['data'], ObjectId):
        db = get_db()
        fs = gridfs.GridFS(db)
        serialized = fs.get(item['data'])
        item['data'] = json.loads(serialized.decode())

    return item


def get_updates(channel_type, channel_instance_id, transaction):
    db = get_db()
    transactions = db[channel_type]
    result = transactions.find({
        'channel_instance_id': channel_instance_id,
        'transaction': { '$gt': transaction },
    })

    return [ _unpack(item) for item in result ]


def get_instance(channel_type, channel_instance_id, instance_type, instance_id=None):
    db = get_db()
    transactions = db[channel_type]
    criteria = {
        'channel_instance_id': channel_instance_id,
        'instance_type': instance_type,
    }
    if instance_id is not None:
        criteria['instance_id'] = instance_id

    instance = transactions.find_one(criteria)
    if not instance:
        return

    return _unpack(instance)


def list_instances(channel_type, channel_instance_id, instance_type, operation=None):
    db = get_db()
    transactions = db[channel_type]
    criteria = {
        'channel_instance_id': channel_instance_id,
        'instance_type': instance_type,
    }
    if operation:
        criteria['operation'] = operation

    instances = transactions.find(criteria)
    if not instances:
        return

    for instance in instances:
        yield _unpack(instance)
