import matplotlib
matplotlib.use('Agg')

from flask import Flask, request, Response
from enum import Enum
import datetime
import dateutil
from dateutil import parser
import json
import os.path
import logging
import random
import requests
import re
import sys
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import io

# TODO
# logging is not very efficient for large number of events used and can be improve
# operations like >rename< are not necessarily sanitized, as the bot is only used with truste users at the moment
# could use command line arguments

'''
Static settings.
'''
state_file = "state.json"
log_file = "coffee.log"
coffee_response_phrases = ["KAAAFFFEEEE", "Enjoy :)"]
tea_response_phrases = ["Splendid!", "Enjoy :)"]
log_level = logging.DEBUG
bot_id = "" # set this to your bot id format "bot<numbers>:<numbers_and_text>"

# if there are no users, this standard user will be created
defautl_user_id="" # set this to the telgram user id of your default user
default_uesr_name="" # set this to the display name of the default user

'''
Setup Logger.
'''
logger = logging.getLogger()
logger.setLevel(log_level)

fh = logging.FileHandler(log_file)
fh.setLevel(log_level)
ch = logging.StreamHandler()
ch.setLevel(log_level)
formatter = logging.Formatter("[%(asctime)s - %(levelname)s] %(message)s")
fh.setFormatter(formatter)
ch.setFormatter(formatter)
logger.addHandler(fh)
logger.addHandler(ch)


'''
Define global variables and setup flask app.
'''
users = {}
app = Flask(__name__)


'''
Define Enumerations for constants, commands etc.
'''


class UserState(Enum):
    """Defines the state that a user can be in when issuing commands"""
    DEFAULT = 0
    PLOT_CUMULATIVE_DATE_PICKER = 1
    PLOT_PER_HOUR_DATE_PICKER = 2
    RENAME = 3


class Keyboard(Enum):
    """Defines an enum for the possible keyboards to be shown to a user."""
    DEFAULT = 0
    MORE = 1
    STATS = 2
    STATS_DATE_CHOOSER = 3


class Command(Enum):
    """Defines the possible commands that can be issued from a user to the bot."""
    addCoffee = 0
    removeCoffee = 10
    invalid = 2
    addUser = 3
    broadcast = 5
    getFile = 6
    moreKeyboard = 7
    backKeyboard = 8
    statisticsKeyboard = 9
    plot = 11
    plot_cumulative = 12
    plot_per_hour = 13
    rename_start = 14
    rename_finish = 15
    addTea = 16
    removeTea = 17
    currentStateCoffee = 20
    currentStateTea = 21
    changeUpdateSettingCoffee = 41
    changeUpdateSettingTea = 42

# maps the command string to the actual command
str_to_command = {u"\u2615": Command.addCoffee, "?": Command.currentStateCoffee, u"\u2615Updates": Command.changeUpdateSettingCoffee,
                  "broadcast": Command.broadcast, "get": Command.getFile, "more": Command.moreKeyboard,
                  "back": Command.backKeyboard, "statistics": Command.statisticsKeyboard,
                  u"-\u2615": Command.removeCoffee, "plot": Command.plot, "rename": Command.rename_start,
                  u"\U0001F375": Command.addTea, u"-\U0001F375": Command.removeTea,
                  u"\u2615?": Command.currentStateCoffee, u"\U0001F375?": Command.currentStateTea,
                  u"\U0001F375Updates": Command.changeUpdateSettingTea}

# commands which need admin rights
admin_commands = [Command.addUser, Command.broadcast, Command.getFile]


class Role(Enum):
    """Defines the roles a user can have."""
    user = 0
    admin = 1


'''
Class definitions for the application logic
'''


class User:
    """Represents a user within the application"""
    def __init__(self, name, role=Role.user, updates_coffee=True, updates_tea=True):
        self.name = name
        self.coffees = []
        self.teas = []
        self.role = role
        self.updates_coffee = updates_coffee
        self.updates_tea = updates_tea
        self.current_keyboard = Keyboard.DEFAULT
        self.state = UserState.DEFAULT

    def add_coffee(self):
        self.coffees.append(datetime.datetime.now())

    def add_tea(self):
        self.teas.append(datetime.datetime.now())

    def remove_last_coffee(self):
        self.coffees = self.coffees[:-1]

    def remove_last_tea(self):
        self.teas = self.teas[:-1]


'''
Define Json Encoder and Decoder. They allow to directly read and write the class structure to/from JSON.
'''


class CoffeeJsonEncoder(json.JSONEncoder):
    """Encode objects to JSON. Complex types get an additional _type attribute to identify them."""
    def default(self, o):
        if isinstance(o, datetime.datetime):
            return {"_type": "datetime", "ctime": o.ctime()}
        elif isinstance(o, User):
            d = o.__dict__
            d["_type"] = "User"
            return d
        elif isinstance(o, Role):
            return {"_type": "Role", "key": o.value}
        elif isinstance(o, Enum):
            pass
        else:
            return o.__dict__


class CoffeeJsonDecoder(json.JSONDecoder):
    """Decodes JSON to objects. Default decoding, unless there is a _type attribute."""

    def __init__(self, *args, **kwargs):
        json.JSONDecoder.__init__(self, object_hook=self.object_hook, *args, **kwargs)

    def object_hook(self, obj):
        if '_type' not in obj:
            return obj
        _type = obj["_type"]
        if _type == "datetime":
            return parser.parse(obj["ctime"])
        elif _type == "User":
            u = User(obj["name"], obj["role"], obj["updates_coffee"], obj["updates_tea"])
            u.coffees = obj["coffees"]
            if "teas" in obj:
                u.teas = obj["teas"]
            else:
                u.teas = []
            return u
        elif _type == "Role":
            return Role(obj["key"])
        else:
            return obj


'''
Application logic.
'''


def store():
    """Stores the current state (global users variable) to a JSON file."""
    logger.debug("saving to file")
    file_handle = open(state_file, "w+")
    file_handle.write(json.dumps(users, cls=CoffeeJsonEncoder))
    file_handle.close()


@app.route("/coffee/" + bot_id, methods=["POST"])
def bot_request():
    """
    Handle web-hook calls form telegram API
    Returns empty responses to complete HTTP request. Answers are send via POSTS to API handles.
    """
    logger.debug("got json: " + str(request.json))
    message = request.json["message"]
    user_id = str(message["from"]["id"])

    if user_id not in users:
        logger.info("unregistered userId: " + user_id)
        # unregistered user - no response
        return Response()

    command, argument = parse_message(message, user_id)
    users[user_id].state = UserState.DEFAULT
    users[user_id].current_keyboard = Keyboard.DEFAULT
    if not check_permissions(command, user_id):
        # user does not have permission for command
        logger.info("command not allowed: {0} by {1}".format(command, user_id))
        send_message(user_id, "Command not allowed")
        return Response()

    execute_command(command, argument, user_id)
    logger.debug("success command - responding")
    return Response()


def send_message(to, text, keyboard=None):
    """Sends a message to the specified user."""
    if keyboard is None:
        keyboard = create_keyboard(to)
    data = {"chat_id": int(to), "text": text, "reply_markup": json.dumps(keyboard)}
    logger.debug("sending message: " + json.dumps(data))
    response = requests.post(
        url="https://api.telegram.org/" + bot_id + "/sendMessage", data=data)
    logger.debug("got response from API: " + str(response.json()))


def send_document(to, f, keyboard=None):
    """Send a document (file path) to the specified user."""
    if keyboard is None:
        keyboard = create_keyboard(to)
    document = open(f, "r+")
    data = {"chat_id": int(to), "reply_markup": json.dumps(keyboard)}
    logger.debug("sending file: " + f)
    response = requests.post(
        url="https://api.telegram.org/" + bot_id + "/sendDocument",
        data=data, files={"document":  (file, document)})
    logger.debug("got response from API: " + str(response.json()))
    document.close()


def send_photo(to, name, buf, keyboard=None):
    if keyboard is None:
        keyboard = create_keyboard(to)
    """Send a photo from a byte buffer to the specified user."""
    data = {"chat_id": int(to), "reply_markup": json.dumps(keyboard)}
    logger.debug("sending photo: " + name)
    response = requests.post(
        url="https://api.telegram.org/" + bot_id + "/sendPhoto",
        data=data, files={"photo":  (name, buf)})
    logger.debug("got response from API: " + str(response.json()))


def check_permissions(command, user_id):
    """Checks whether the user can execute the given command."""
    if command in admin_commands:
        return users[user_id].role == Role.admin
    else:
        return True


def parse_message(message, user_id):
    """Extracts the command (and optionally arguments) from the current message."""
    command = Command.invalid
    argument = None
    if users[user_id].state == UserState.DEFAULT:
        if "text" in message:  # normal command
            text = message["text"]
            tokens = re.split("\s+", text)
            token = tokens[0].replace(":", "")
            if token in str_to_command:
                command = str_to_command[token]
                if len(tokens) > 1:
                    text = text.lstrip()
                    text = text[len(token):]
                    text = text.strip()
                    if len(text) >= 1:
                        argument = text
        elif "contact" in message:  # user-add command
            command = Command.addUser
            argument = {"user_id": str(message["contact"]["user_id"]), "name": message["contact"]["first_name"]}
    elif users[user_id].state == UserState.PLOT_CUMULATIVE_DATE_PICKER or \
                    users[user_id].state == UserState.PLOT_PER_HOUR_DATE_PICKER:
        try:
            text = message["text"]
            if users[user_id].state == UserState.PLOT_CUMULATIVE_DATE_PICKER:
                command = Command.plot_cumulative
            elif users[user_id].state == UserState.PLOT_PER_HOUR_DATE_PICKER:
                command = Command.plot_per_hour
            if text == "All":
                argument = "All"
            else:
                argument = parser.parse(text)
        except:
            logger.info("got unexpected error when parsing user plot command: {0}".format(sys.exc_info()[0]))
    elif users[user_id].state == UserState.RENAME:
        command = Command.rename_finish
        if "text" in message and len(message["text"]) > 0:
            argument = message["text"][:15]
        else:
            command = Command.invalid
            argument = None
    return command, argument


def create_keyboard(user_id):
    """Creates a keyboard for the given user at the current time."""
    if users[user_id].current_keyboard == Keyboard.MORE:
        return {"keyboard": [[u"-\u2615", u"-\U0001F375"], ["statistics", "rename"], ["back"]], "resize_keyboard": True}
    elif users[user_id].current_keyboard == Keyboard.STATS:
        return {"keyboard": [["plot cumulative count"], ["plot coffee per time of day"], ["back"]],
                "resize_keyboard": True}
    elif users[user_id].current_keyboard == Keyboard.STATS_DATE_CHOOSER:
        current_month = datetime.date.today()
        current_month = current_month.replace(day=1)
        month_1_before = current_month - dateutil.relativedelta.relativedelta(months=1)
        month_2_before = current_month - dateutil.relativedelta.relativedelta(months=2)
        buttons = [["All"]] + map(lambda x: [x.strftime("%b %Y")], [current_month, month_1_before, month_2_before])\
                  + [["back"]]
        return {"keyboard": buttons, "resize_keyboard": True}
    else:  # Keyboard.DEFAULT or otherwise
        if users[user_id].updates_coffee:
            update_text_coffee = u"\u2615Updates [on]"
        else:
            update_text_coffee = u"\u2615Updates [off]"
        if users[user_id].updates_tea:
            update_text_tea = u"\U0001F375Updates [on]"
        else:
            update_text_tea = u"\U0001F375Updates [off]"
        return {"keyboard": [[u"\u2615", u"\U0001F375"], [u"\u2615?", u"\U0001F375?"], [update_text_coffee, update_text_tea], ["more"]], "resize_keyboard": True}


def current_state_coffee():
    """Create a string with the current coffee counts."""
    now = datetime.datetime.now()
    output = {}
    for user in users.values():
        coffees = list(filter(lambda x: x.month == now.month and x.year == now.year, user.coffees))
        output[user.name] = len(coffees)
    lines = [name + ": "+str(c) for (name, c) in sorted(output.items(), key=lambda x: -x[1])]
    return u"\u2615\n" + "\n".join(lines)


def current_state_tea():
    """Create a string with the current coffee counts."""
    now = datetime.datetime.now()
    output = {}
    for user in users.values():
        teas = list(filter(lambda x: x.month == now.month and x.year == now.year, user.teas))
        output[user.name] = len(teas)
    lines = [name + ": " + str(t) for (name, t) in sorted(output.items(), key=lambda x: -x[1])]
    return u"\U0001F375\n" + "\n".join(lines)


def execute_command(command, argument, user_id):
    """Execute the given commands."""
    if command == Command.addCoffee:
        logger.debug("Executing 'addCoffee' for " + user_id)
        users[user_id].add_coffee()
        store()
        phrase = random.choice(coffee_response_phrases)
        send_message(user_id, phrase + "\n\n" + current_state_coffee())
        for u in users:  # send updates
            update_text = u"{0} just had coffee. And that is great.\n\n{1}".format(users[user_id].name, current_state_coffee())
            if u != user_id and users[u].updates_coffee:
                send_message(u, update_text)
    elif command == Command.removeCoffee:
        logger.debug("Removing last coffee for " + user_id)
        users[user_id].remove_last_coffee()
        store()
        send_message(user_id, current_state_coffee())
    elif command == Command.addTea:
        logger.debug("Executing 'addTea' for " + user_id)
        users[user_id].add_tea()
        store()
        phrase = random.choice(tea_response_phrases)
        send_message(user_id, phrase + "\n\n" + current_state_tea())
        for u in users:  # send updates
            update_text = u"{0} just had tea. And that is splendid.\n\n{1}".format(users[user_id].name, current_state_tea())
            if u != user_id and users[u].updates_tea:
                send_message(u, update_text)
    elif command == Command.removeTea:
        logger.debug("Removing last tea for " + user_id)
        users[user_id].remove_last_tea()
        store()
        send_message(user_id, current_state_tea())
    elif command == Command.currentStateCoffee:
        logger.debug("Executing 'currentStateCoffee' for " + user_id)
        send_message(user_id, current_state_coffee())
    elif command == Command.currentStateTea:
        logger.debug("Executing 'currentStateTea' for " + user_id)
        send_message(user_id, current_state_tea())
    elif command == Command.changeUpdateSettingTea:
        logger.debug("Executing 'changeUpdateTea: {0}' for {1}".format(argument, user_id))
        if argument == "[off]":
            users[user_id].updates_tea = True
            send_message(user_id, "Tea updates enabled")
        elif argument == "[on]":
            users[user_id].updates_tea = False
            send_message(user_id, "Tea updates disabled")
        store()
    elif command == Command.changeUpdateSettingCoffee:
        logger.debug("Executing 'changeUpdateCoffee: {0}' for {1}".format(argument, user_id))
        if argument == "[off]":
            users[user_id].updates_coffee = True
            send_message(user_id, "Coffee updates enabled")
        elif argument == "[on]":
            users[user_id].updates_coffee = False
            send_message(user_id, "Coffee updates disabled")
        store()
    elif command == Command.addUser:
        logger.debug("Executing 'addUser: {0}' for {1}".format(argument, user_id))
        users[argument["user_id"]] = User(argument["name"])
        store()
        send_message(argument["user_id"], "You have been added to the cofeebot")
        for u in users:
            if u != argument["user_id"]:
                send_message(u, "Successfully added {0} to the Bot. Welcome!".format(argument["name"]))
    elif command == Command.moreKeyboard:
        logger.debug("setting keyboard to more for " + user_id)
        users[user_id].current_keyboard = Keyboard.MORE
        send_message(user_id, "showing more option")
    elif command == Command.backKeyboard:
        logger.debug("setting keyboard to default for " + user_id)
        users[user_id].current_keyboard = Keyboard.DEFAULT
        send_message(user_id, "back to default menu")
    elif command == Command.statisticsKeyboard:
        logger.debug("setting keyboard to stats for " + user_id)
        users[user_id].current_keyboard = Keyboard.STATS
        send_message(user_id, "statistics")
    elif command == Command.rename_start:
        logger.debug("initiating rename for " + user_id)
        users[user_id].current_keyboard = Keyboard.DEFAULT
        users[user_id].state = UserState.RENAME
        send_message(user_id, "please enter the new name", {"remove_keyboard": True})
    elif command == Command.rename_finish:
        logger.debug("finishing rename for " + user_id + "to " + argument)
        users[user_id].name = argument
        store()
        send_message(user_id, "renamed to " + argument)
    elif command == Command.broadcast:
        logger.info("sending broadcast " + argument)
        for u in users:
            send_message(u, argument)
    elif command == Command.getFile:
        if argument == "state":
            logger.debug("sending state file")
            send_document(user_id, state_file)
        elif argument == "log":
            logger.debug("sending log file")
            send_document(user_id, log_file)
    elif command == Command.plot:
        if argument.startswith("cumulative"):
            logger.debug("setting plot mode to cumulative; displaying date picker for " + user_id)
            users[user_id].state = UserState.PLOT_CUMULATIVE_DATE_PICKER
            users[user_id].current_keyboard = Keyboard.STATS_DATE_CHOOSER
            send_message(user_id, "please specify the data range")
        elif argument.startswith("coffee"):
            logger.debug("setting plot mode to cumulative; displaying date picker for " + user_id)
            users[user_id].state = UserState.PLOT_PER_HOUR_DATE_PICKER
            users[user_id].current_keyboard = Keyboard.STATS_DATE_CHOOSER
            send_message(user_id, "please specify the data range")
        else:
            logger.debug("invalid plot mode ({0}); resetting to default".format(argument))
            users[user_id].current_keyboard = Keyboard.DEFAULT
            send_message(user_id, "invalid selection")
    elif command == Command.plot_cumulative:
        logger.debug("Creating cumulative plot, argument: {0}".format(argument))
        any_data = False
        figure = plt.figure(figsize=(8.5, 6))
        for u in users:
            name = users[u].name
            if argument == "All":
                coffees = users[u].coffees
            else:
                coffees = list(filter(lambda x: x.month == argument.month and x.year == argument.year, users[u].coffees))
            count = [1] * len(coffees)
            count = np.cumsum(count)
            ser = pd.Series(count, coffees)
            if len(ser) > 0:
                ser.plot(label=name)
                any_data = True
        if any_data:
            handles, labels = figure.axes[0].get_legend_handles_labels()
            figure.axes[0].legend(handles[::-1], labels[::-1], bbox_to_anchor=(1.05, 1), loc=2, borderaxespad=0.)
            if argument == "All":
                figure.axes[0].set_title("coffee counts over time")
            else:
                figure.axes[0].set_title("coffee count in {0}".format(argument.strftime("%B %Y")))
            figure.axes[0].set(ylabel='#coffees')
            figure.axes[0].set(xlabel='date')

            figure.subplots_adjust(right=0.8, bottom=0.2)

            buf = io.BytesIO()
            plt.savefig(buf, format='png')
            buf.seek(0)
            send_photo(user_id, "coffee_count.png", buf)
            buf.close()
        else:
            send_message(user_id, "on data for the given time interval")
    elif command == Command.plot_per_hour:
        logger.debug("Creating per hour plot, argument: {0}".format(argument))
        any_data = False
        figure = plt.figure(figsize=(8.5,6))
        for u in users:
            name = users[u].name
            weekday = []
            time = []
            if argument == "All":
                coffees = users[u].coffees
            else:
                coffees = list(filter(lambda x: x.month == argument.month and x.year == argument.year, users[u].coffees))
            if len(coffees) > 0:
                any_data = True
                for c in users[u].coffees:
                    weekday.append(c.weekday())
                    time.append(c.hour + c.minute/60.0 + c.second/(60.0*60.0))
                plt.scatter(weekday, time, marker="x", label=name)
        if any_data:
            handles, labels = figure.axes[0].get_legend_handles_labels()
            figure.axes[0].legend(handles[::-1], labels[::-1], bbox_to_anchor=(1.05, 1), loc=2, borderaxespad=0.)
            if argument == "All":
                figure.axes[0].set_title("coffee consummation by time of day")
            else:
                figure.axes[0].set_title("coffee consummation by time of day in {0}".format(argument.strftime("%B %Y")))
            figure.axes[0].set(ylabel="time of day")
            figure.axes[0].set(xlabel="day of week")
            figure.axes[0].set_ylim([0, 23])
            plt.xticks(range(7), ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'], rotation='vertical')
            plt.yticks(range(24), range(24))
            plt.gca().invert_yaxis()

            figure.subplots_adjust(right=0.8, bottom=0.2)

            buf = io.BytesIO()
            plt.savefig(buf, format='png')
            buf.seek(0)
            send_photo(user_id, "coffee_per_hour.png", buf)
            buf.close()
        else:
            send_message(user_id, "on data for the given time interval")
    else:
        pass


'''
Start Application
'''

if __name__ == "__main__":
    if os.path.exists(state_file):  # if state file exist load
        logger.info("Found existing state file. Loading.")
        f = open(state_file, "r+")
        users = json.loads(f.read(), cls=CoffeeJsonDecoder)
        f.close()
        logger.info("Loaded: " + current_state_coffee() + "\n" +current_state_tea())
    else:  # else add default user as admin and create new user list
        users[defautl_user_id] = User(default_uesr_name, Role.admin)
    app.run(port=8080, debug=False)
    store()
