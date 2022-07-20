#!/usr/bin/env python3
"""This script is...."""

__author__ = "Aaron Davis"
__version__ = "0.1.5"
__copyright__ = "Copyright (c) 2022 Aaron Davis"
__license__ = "MIT License"

import configparser
import logging
from datetime import datetime, date, timedelta, timezone
import os
from socket import MsgFlag
import sys
import json
from time import time
import requests
import certifi
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure

config = configparser.ConfigParser()
config.read("config.ini")
mongoaddr = config["MONGO"]["mongo_addr"]
mongodb = config["MONGO"]["mongo_db"]
mongouser = config["MONGO"]["user_name"]
mongopw = config["MONGO"]["password"]
webex_bearer = config["WEBEX"]["bearer"]

MAX_MONGODB_DELAY = 500

Mongo_Client = MongoClient(
    f"mongodb+srv://{mongouser}:{mongopw}@{mongoaddr}/{mongodb}?retryWrites=true&w=majority",
    tlsCAFile=certifi.where(),
    serverSelectionTimeoutMS=MAX_MONGODB_DELAY,
)

db = Mongo_Client[mongodb]
discards = db["discards"]

wa_token = f"Bearer {webex_bearer}"
wa_headers = {"Authorization": wa_token, "Content-Type": "application/json"}

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(r".\logs\debug.log"),
        logging.StreamHandler(sys.stdout),
    ],
)


def update_created(recerd, date_string):
    """When the 'createdAt' date is created in Mongo, it is a str. This function changes the
    MongoDB type to 'date'. This is required for a MongoDB index job that purges records
    older than 7-days. This index job is from managing the size of the Mongo database.

    Args:
        record (int): MongoDB record object _id
        date_string (str): The records created time to be converted
    """
    try:
        mydate = datetime.strptime(date_string, "%Y-%m-%dT%H:%M:%S.%f%z")
        discards.update_one({"_id": recerd}, {"$set": {"card_created": mydate}})
    except ConnectionFailure as key_error:
        print(key_error)


def msg_stale(card, key):
    """This function is looking for abandoned adaptive cards generated by the [PSQRT bot](https://github.com/dirflash/psirt-bot-card).

    Args:
        card (str): Webex message ID to validate
        key (str): MongoDB record _id

    Returns:
       list: MongoDB record ids of abandoned adaptive cards, or 'False" if message ID not valid (already deleted)
    """
    get_msg_url = f"https://webexapis.com/v1/messages/{card}"

    get_msg = requests.request(
        "GET",
        get_msg_url,
        headers=wa_headers,
    )
    gm_sc = get_msg.status_code
    print(f"Get Msg Status: {gm_sc}")
    update_msg_status = discards.update_one(
        {"_id": key}, {"$set": {"msg_status": gm_sc}}
    )
    print(f"Updated count: {update_msg_status.modified_count}")
    if gm_sc == 404:
        return key
    return False


def del_aband(del_id, del_head, mon_id):
    """Delete abandoned adaptive cards that haven't been submitted in 10 minutes

    Args:
        del_id (str): Webex message ID
        del_head (str): Webex message requests header
        mon_id (_id): MongoDB record ID of message
    """
    del_msg_url = f"https://webexapis.com/v1/messages/{del_id}"
    del_msg = requests.request(
        "DELETE",
        del_msg_url,
        headers=del_head,
    )
    dl_sc = del_msg.status_code
    print(f"Get Msg Status: {dl_sc}")
    if dl_sc == 204:
        del_msg = discards.update_one(mon_id, {"$set": {"card_deleted": True}})
        print(f"Card deleted count: {del_msg.deleted_count}")


num_records = discards.count_documents({"_id": {"$exists": True}})
print(f"{num_records=}")

# Get the new record ID's in Mongo
new_record_ids = []
record_ids = []
cards = {}
stale_msgs = []
abandoned_msgs = []

new_record = discards.find({"card_created": {"$type": "string"}})
all_records = discards.find({"card_id": {"$exists": True}})

for _ in all_records:
    rec_id = _.get("_id")
    ids = {"_id": rec_id}
    record_ids.append(rec_id)

for record in new_record:
    ID = record.get("_id")
    record_id = {"_id": ID}
    new_record_ids.append(ID)

for cnt, value in enumerate(new_record_ids):
    up_record = discards.find_one({"_id": value})
    date_str = up_record["card_created"]
    if isinstance(date_str, str):
        update_created(value, date_str)
    card_id = up_record["card_id"]

for cnt, value in enumerate(record_ids):
    a_record = discards.find_one({"_id": value})
    card_id = a_record["card_id"]
    cards[value] = card_id
    if "msg_status" not in a_record:
        print(f"{value} needs to be updated.")
        stale_list = msg_stale(card_id, value)
        stale_msgs.append(stale_list)

for idx, val in enumerate(stale_msgs):
    if val is False:
        stale_msgs.pop(idx)
    else:
        record_id = {"_id": val}
        delete_msg = discards.delete_one(record_id)
        print(f"Deleted count: {delete_msg.deleted_count}")

msg_offset = datetime.now() - timedelta(minutes=10)
abandoned_msg = discards.find({"msg_status": {"$exists": True}})

for ind, item in enumerate(abandoned_msg):
    ID = item.get("_id")
    item_id = {"_id": ID}
    abandoned_msgs.append(ID)
    m_status = item["msg_status"]
    if m_status == 200:
        m_time = item["card_created"]
        m_id = item["card_id"]
        print(m_time)
        if msg_offset > m_time:
            print("Abandoned card")
            del_aband(m_id, wa_headers, item_id)
