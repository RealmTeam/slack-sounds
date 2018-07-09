#!/usr/bin/env python
# -*- coding: utf-8 -*-
import time
import subprocess
from datetime import datetime
from distutils.util import strtobool
import os
import re
import json
import urllib2

from slackclient import SlackClient

BASE_DIR = os.path.dirname(os.path.realpath(__file__))
SOUNDS_DIR = os.path.join(BASE_DIR, 'sounds')
CONFIG_FILE = os.path.join(BASE_DIR, 'config.json')

PLAYER = 'mpg123'
FILETYPE = 'mp3'
DEFAULT_OPTIONS = {
    "_token": None,
    "throttling": True,
    "throttling_reset": 10 * 60,
    "throttling_count": 5
}

PLAY_REGEX = re.compile(u"play\s([a-z0-9_'’ -]+)", re.IGNORECASE)
REMOVE_REGEX = re.compile(u"remove\s([a-z0-9_'’ -]+)", re.IGNORECASE)
UPDATE_CONF_REGEX = re.compile("^set ([A-Z0-9_]+) to ([A-Z0-9_]+)$", re.IGNORECASE)
SHOW_CONF_REGEX = re.compile("^show conf$", re.IGNORECASE)
LIST_SOUNDS_REGEX = re.compile("list\ssounds", re.IGNORECASE)



def load_config():
    config = {}
    with open(CONFIG_FILE, 'r') as f:
        config = json.loads(f.read())
    for key, value in DEFAULT_OPTIONS.iteritems():
        config.setdefault(key, value)
    return config


def write_config(config):
    with open(CONFIG_FILE, 'w') as f:
        f.write(json.dumps(config))


def find_sound(sound_name):
    directories = (file_ for file_ in os.listdir(SOUNDS_DIR)
                   if os.path.isdir(os.path.join(SOUNDS_DIR, file_)))
    for d in directories:
        path = os.path.join(SOUNDS_DIR, d, '{}.{}'.format(sound_name.replace(' ', '_'), FILETYPE))
        if os.path.isfile(path):
            return path


def play_action(match, user, config):
    sound_name = match.group(1).strip().replace(u"’", "'")
    sound_file = find_sound(sound_name)

    def throttle():
        if not config["throttling"] or user["is_admin"]:
            return False, None
        record = throttling_record.get(user["name"], {"time": time.time(), "count": 0})
        if (time.time() - record["time"]) < config["throttling_reset"]:
            record["count"] += 1
        else:
            record["count"] = 1
            record["time"] = time.time()
        throttling_record[user["name"]] = record
        return record["count"] > config["throttling_count"], record

    if sound_file:
        throttled, record = throttle()
        if throttled:
            message = 'You reached your throttling limit. Try again later.'
        else:
            message = 'Playing ' + sound_name
            subprocess.Popen([PLAYER, "{}".format(sound_file)])
        if record:
            message += '\n {} plays left. Reset at {}.'.format(
                config["throttling_count"] - record["count"],
                datetime.fromtimestamp(record["time"] + config["throttling_reset"]).strftime('%H:%M:%S')
            )
    else:
        message = 'No sound matching ' + sound_name
    return message


def remove_action(match, user, config):
    if not user["is_admin"]:
        return
    sound_name = match.group(1).strip().replace(u"’", "'")
    sound_file = find_sound(sound_name)
    if sound_file:
        os.remove(sound_file)
        message = 'Removed ' + sound_name
    else:
        message = 'No sound matching ' + sound_name
    return message


def list_sounds_action(match, user, config):
    message = '```\nAvailable sounds are :\n'
    directories = sorted(file_ for file_ in os.listdir(SOUNDS_DIR)
                         if os.path.isdir(os.path.join(SOUNDS_DIR, file_)))

    def split_by_cols(l, n=4):
        output = ''
        for row in (l[i:i + n] for i in xrange(0, len(l), n)):
            fmt = "| {:<30s} " * len(row)
            output += fmt.format(*row) + '\n'
        return output

    for directory in directories:
        message += '\n' + directory.upper() + ':\n'
        sounds = sorted(s.split('.')[0].replace('_', ' ') for s in os.listdir(os.path.join(SOUNDS_DIR, directory)))
        message += split_by_cols(sounds)

    message += '```'
    return message


def show_conf_action(match, user, config):
    if not user["is_admin"]:
        return
    message = ''
    for key, value in config.iteritems():
        message += '{}: {}\n'.format(key, value)
    return message


def update_conf_action(match, user, config):
    if not user["is_admin"]:
        return
    key = match.group(1)
    value = match.group(2)
    if key.startswith('_'):
        return "Can't set private variables"
    try:
        value = int(value)
    except ValueError:
        try:
            value = bool(strtobool(value))
        except ValueError:
            pass
    config[key] = value
    write_config(config)
    return "Config set"


ACTIONS = {
    PLAY_REGEX: play_action,
    REMOVE_REGEX: remove_action,
    UPDATE_CONF_REGEX: update_conf_action,
    SHOW_CONF_REGEX: show_conf_action,
    LIST_SOUNDS_REGEX: list_sounds_action,
}

def add_sound(file_id, config):
    info = sc.api_call("files.info", file=file_id)
    file_url = info.get("file").get("url_private") if info["ok"] else ''
    filename = info.get("file").get("title") if info["ok"] else ''
    if filename.endswith('.mp3') and file_url.endswith('.mp3'):
        folder = 'misc'
        if ':' in filename:
            folder, filename = filename.split(':')
        try:
            os.makedirs(os.path.join(SOUNDS_DIR, folder.strip()))
        except OSError:
            pass
        req = urllib2.Request(file_url, headers={"Authorization": "Bearer " + config["_token"]})
        with open(os.path.join(SOUNDS_DIR, folder.strip(), filename.strip()), 'w+') as f:
            f.write(urllib2.urlopen(req).read())


def load_users():
    user_list = sc.api_call("users.list")
    users = {}
    for user in user_list["members"]:
        users[user["id"]] = {
            "name": user["name"],
            "is_admin": user.get("is_admin", False),
            "id": user["id"]
        }
    return users


if __name__ == '__main__':
    throttling_record = {}
    config = load_config()
    sc = SlackClient(config["_token"])
    if sc.rtm_connect():
        bot_id = sc.api_call("auth.test")["user_id"]
        users = load_users()
        while True:
            for event in sc.rtm_read():
                event_type = event.get('type', None)

                if event_type == 'message':
                    text = event.get('text', '')
                    user = users.get(event.get('user', None), None)
                    channel = event.get('channel', None)
                    if not user or not text or not channel:
                        continue

                    message = None
                    for regex, action in ACTIONS.iteritems():
                        match = regex.match(text)
                        if match:
                            message = action(match, user, config)
                            break

                    if message:
                        sc.api_call("chat.postEphemeral", channel=channel, text=message, user=user["id"])

                elif event_type == 'file_created' or event_type == 'file_shared':
                    file_id = event.get('file', {}).get('id', None)
                    if file_id:
                        add_sound(file_id, config)
            time.sleep(1);
    else:
        print 'Connection failed, invalid token?'
