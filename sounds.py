#!/usr/bin/env python
# -*- coding: utf-8 -*-
import time
import traceback
import subprocess
import string
from datetime import datetime
from distutils.util import strtobool
import os
import re
import json
import urllib.request, urllib.error, urllib.parse

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler


BASE_DIR = os.path.dirname(os.path.realpath(__file__))
SOUNDS_DIR = os.path.join(BASE_DIR, 'sounds')
CONFIG_FILE = os.path.join(BASE_DIR, 'config.json')
LOGGING_FILE = os.path.join(BASE_DIR, 'commands.log')
VALID_CHARS = string.ascii_letters + string.digits + " .'_-"
FOLDER_SEP = ':/|'
PLAYER = 'mpg123'
FILETYPE = 'mp3'
EQUALIZER = ['mp3gain', '-r']
PAD_SILENCE = ['sox', 'in.mp3', 'out.mp3', 'pad', '0.5', '0']
TRIM = ['sox', 'in.mp3', 'out.mp3', 'trim', 'from', 'to']
FADE = ['sox', 'in.mp3', 'out.mp3', 'fade', '0', '-0', '2']
YOUTUBE_DOWNLOAD = ['youtube-dl', '--extract-audio', '--audio-format', 'mp3', 'url', '-o', '{}.%(ext)s']

DEFAULT_OPTIONS = {
    "_token": None,
    "throttling": True,
    "throttling_reset": 10 * 60,
    "throttling_count": 5,
    "default_ban_length": 30,
}

PLAY_REGEX = re.compile("play\s([a-z0-9_' ]+)", re.IGNORECASE)
REMOVE_REGEX = re.compile("remove\s([a-z0-9_' ]+)", re.IGNORECASE)
UPDATE_CONF_REGEX = re.compile("^set\s([A-Z0-9_]+)\sto\s([A-Z0-9_]+)$", re.IGNORECASE)
SHOW_CONF_REGEX = re.compile("^show\sconf$", re.IGNORECASE)
LIST_SOUNDS_REGEX = re.compile("list\ssounds", re.IGNORECASE)
PUNISH_USER_REGEX = re.compile("punish\s<?@([A-Z0-9_-]+)>?\s?(\d+)?", re.IGNORECASE)
HELP_REGEX = re.compile("^help$", re.IGNORECASE)
SHOW_LOGS_REGEX = re.compile("^show\slogs$", re.IGNORECASE)
TRIM_REGEX = re.compile("^trim\s([a-z0-9_' ]+)\s([\d\.]+)\s([\d\.]+)$", re.IGNORECASE)
FADE_OUT_REGEX = re.compile("^fade\s([a-z0-9_' ]+)$", re.IGNORECASE)
YOUTUBE_REGEX = re.compile("^download\s<?(https?://[^\s/$.?#].[^\s]*)>?\s([a-z0-9_' :/|]+)$", re.IGNORECASE)
PAD_REGEX = re.compile("^pad\s([a-z0-9_' ]+)$", re.IGNORECASE)


users = {}
throttling_record = {}
punished = {}
logs = []
config = {}
with open(CONFIG_FILE, 'r') as f:
    config = json.loads(f.read())
for key, value in DEFAULT_OPTIONS.items():
    config.setdefault(key, value)
app = App(token=config["oauth_token"])


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
    sound_name = match.group(1).strip()
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

    def check_punished():
        if user["is_admin"]:
            return False
        release = punished.get(user["name"], time.time())
        if release > time.time():
            return release
        return False

    if sound_file:
        throttled, record = throttle()
        punished_release = check_punished()
        if throttled:
            message = 'You reached your throttling limit. Try again later.'
        elif punished_release:
            message = 'You have been punished ! No sounds until {}.'.format(datetime.fromtimestamp(punished_release).strftime('%H:%M:%S'))
        else:
            logs.append((user, sound_name, time.time()))
            message = 'Playing ' + sound_name
            subprocess.Popen([PLAYER, "{}".format(sound_file)])
        if record:
            message += '\n {} plays left. Reset at {}.'.format(
                max(config["throttling_count"] - record["count"], 0),
                datetime.fromtimestamp(record["time"] + config["throttling_reset"]).strftime('%H:%M:%S')
            )
    else:
        message = 'No sound matching ' + sound_name
    return message


def remove_action(match, user, config):
    if not user["is_admin"]:
        return
    sound_name = match.group(1).strip()
    sound_file = find_sound(sound_name)
    if sound_file:
        os.remove(sound_file)
        message = 'Removed ' + sound_name
    else:
        message = 'No sound matching ' + sound_name
    return message


def show_logs_action(match, user, config):
    return '\n'.join(['{} played {} at {}'.format(l[0]['name'], l[1], datetime.fromtimestamp(l[2]).strftime('%H:%M:%S'))
                      for l in logs[-10:]])


def list_sounds_action(match, user, config):
    message = '```\nAvailable sounds are :\n'
    directories = sorted(file_ for file_ in os.listdir(SOUNDS_DIR)
                         if os.path.isdir(os.path.join(SOUNDS_DIR, file_)))

    def split_by_cols(l, n=4):
        output = ''
        for row in (l[i:i + n] for i in range(0, len(l), n)):
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
    for key, value in config.items():
        message += '{}: {}\n'.format(key, value)
    return message


def show_help_action(match, user, config):
    message = """
Welcome to sounds, the bot that brings fun to your team.
To interact with the bot, simply use these commands:
    list sounds: shows the full list of all the sounds available
    play replace_with_sound: plays the sound you chose from the list
    show logs: shows a list who played the last 10 sounds
    pad replace_with_sound: adds 0.5s at the beginning of the sound
    trim replace_with_sound 2.5 10: trim the selected sound to be only between 2.5 and 10 seconds
    fade replace_with_sound: adds a 1s fadeout on your sound
    download replace_with_youtube_url replace_with_sound: downloads a sound from youtube
    help: shows this help"""
    if user["is_admin"]:
        message += """
    remove sound_name: removes the sound from the list
    show conf: show the config variables
    set x to y: updates the x config variable with y value
    punish @user 30: prevent user from playing a sound for 30 minutes"""
    message += """
How to upload a sound ?
In the bot channel, upload your mp3 file. This file should already be cut properly and have 0.5s of silence at the beginning.
You can use various websites like sonyoutube.com to convert a youtube video to an mp3 file and then use a software like audacity or a website like audiotrimmer.com to edit it.
Be sure you filename ends with .mp3 and if you want to put your file in a specific folder separate the folder from the filename like so folder:filename.mp3

That's it with the instructions, have fun !"""
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


def punish_user_action(match, user, config):
    if not user["is_admin"]:
        return
    who = match.group(1)
    r = users[who]
    if r:
        who = r
    else:
        return "Couldn't find user {}".format(user)
    try:
        how_long = int(match.group(2) or config.get('default_ban_length'))
    except ValueError:
        how_long = 30
    punished[who["name"]] = time.time() + how_long * 60
    return "{} has been punished for {} minutes.".format(who["name"], how_long)


def trim_action(match, user, config):
    sound_name = match.group(1).strip()
    sound_file = find_sound(sound_name)
    tmp_file = '__NEW__' + os.path.basename(sound_file)
    if sound_file:
        trim_command = list(TRIM)
        trim_command[1] = sound_file
        trim_command[2] = tmp_file
        trim_command[4] = match.group(2)
        trim_command[5] = '=' + match.group(3)
        process = subprocess.Popen(trim_command)
        process.wait()
        os.rename(tmp_file, sound_file)
        message = 'Trimmed ' + sound_name
    else:
        message = 'No sound matching ' + sound_name
    return message


def pad_action(match, user, config):
    sound_name = match.group(1).strip()
    sound_file = find_sound(sound_name)
    tmp_file = '__NEW__' + os.path.basename(sound_file)
    if sound_file:
        pad_command = list(PAD_SILENCE)
        pad_command[1] = sound_file
        pad_command[2] = tmp_file
        process = subprocess.Popen(pad_command)
        process.wait()
        os.rename(tmp_file, sound_file)
        message = 'Padded ' + sound_name
    else:
        message = 'No sound matching ' + sound_name
    return message


def fade_out_action(match, user, config):
    sound_name = match.group(1).strip()
    sound_file = find_sound(sound_name)
    tmp_file = '__NEW__' + os.path.basename(sound_file)
    if sound_file:
        fade_command = list(FADE)
        fade_command[1] = sound_file
        fade_command[2] = tmp_file
        process = subprocess.Popen(fade_command)
        process.wait()
        os.rename(tmp_file, sound_file)
        message = 'Faded ' + sound_name
    else:
        message = 'No sound matching ' + sound_name
    return message


def slugify(raw):
    return "".join([x for x in raw if x in VALID_CHARS]).replace("-", "_").strip().replace(" ", "_").lower()


def download_action(match, user, config):
    url = match.group(1)
    filename = match.group(2)
    folder = 'misc'
    for sep in FOLDER_SEP:
        if sep in filename:
            folder, filename = filename.split(sep)
            break
    if filename.endswith('.mp3'):
        filename = filename[:-4]
    filename = slugify(filename)

    dl_command = list(YOUTUBE_DOWNLOAD)
    dl_command[-1] = dl_command[-1].format(filename)
    dl_command[-3] = url
    process = subprocess.Popen(dl_command)
    process.wait()

    path_to_sound = os.path.join(SOUNDS_DIR, slugify(folder), filename + '.mp3')
    try:
        os.makedirs(os.path.join(SOUNDS_DIR, slugify(folder)))
    except OSError:
        pass
    os.rename(filename + '.mp3', path_to_sound)
    subprocess.Popen(EQUALIZER + [path_to_sound])
    return "Sound added correctly"


def add_sound(sc, file_id, config):
    info = sc.files_info(file=file_id)
    file_url = info.get("file").get("url_private") if info["ok"] else ''
    filename = info.get("file").get("title") if info["ok"] else ''
    if filename.endswith('.mp3') and file_url.endswith('.mp3'):
        folder = 'misc'
        for sep in FOLDER_SEP:
            if sep in filename:
                folder, filename = filename.split(sep)
                break
        try:
            os.makedirs(os.path.join(SOUNDS_DIR, slugify(folder)))
        except OSError:
            pass
        req = urllib.request.Request(file_url, headers={"Authorization": "Bearer " + config["_token"]})
        path_to_sound = os.path.join(SOUNDS_DIR, slugify(folder), slugify(filename))
        with open(path_to_sound, 'w+') as f:
            f.write(urllib.request.urlopen(req).read())
        subprocess.Popen(EQUALIZER + [path_to_sound])


ACTIONS = {
    PLAY_REGEX: play_action,
    REMOVE_REGEX: remove_action,
    UPDATE_CONF_REGEX: update_conf_action,
    SHOW_CONF_REGEX: show_conf_action,
    PUNISH_USER_REGEX: punish_user_action,
    HELP_REGEX: show_help_action,
    LIST_SOUNDS_REGEX: list_sounds_action,
    SHOW_LOGS_REGEX: show_logs_action,
    YOUTUBE_REGEX: download_action,
    PAD_REGEX: pad_action,
    TRIM_REGEX: trim_action,
    FADE_OUT_REGEX: fade_out_action,
}


def load_users(sc):
    user_list = []

    def paginated_api_call(cursor=None):
        response = sc.users_list(cursor=cursor)
        user_list.extend(response.get("members", []))
        if response.get("response_metadata", {}).get("next_cursor"):
            paginated_api_call(response["response_metadata"]["next_cursor"])

    paginated_api_call()
    for user in user_list:
        users[user["id"]] = {
            "name": user["name"],
            "is_admin": user.get("is_admin", False),
            "id": user["id"]
        }

@app.event("file_created")
@app.event("file_shared")
def file_uploaded(event, **kwargs):
    file_id = event.get('file', {}).get('id', None)
    if file_id:
        add_sound(app._client, file_id, config)


@app.event("message")
def message_received(event, **kwargs):
    text = event.get('text', '').replace('’', "'")
    user = users.get(event.get('user', None), None)
    channel = event.get('channel', None)
    if not user or not text or not channel:
        return

    message = None
    for regex, action in ACTIONS.items():
        match = regex.match(text)
        if match:
            message = action(match, user, config)
            if message:
                app._client.chat_postEphemeral(channel=channel, text=message, user=user["id"])
            break


def start():
    handler = SocketModeHandler(app, config["app_token"])
    load_users(app._client)
    bot_id = app._client.auth_test()["user_id"]
    handler.start()



if __name__ == '__main__':
    while True:
        try:
            start()
        except Exception as e:
            traceback.print_exc()
        time.sleep(30)