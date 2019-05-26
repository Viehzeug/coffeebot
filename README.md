# Coffeebot

This repository contains the code for a telegram bot that tallies coffee and tea consummation.
The bot is not used actively anymore and also not developed further. The code is mostly here for posterity.


## Installation
The server can be run with python3 and requires only the dependencies listed in `requirements.txt` (to be installed via `pip`).

Running `server.py` will launch a server, that is accessible via localhost on port 8080. Using it with Telegram requires to set up a proxy (with HTTPS termination etc., e.g. Apache or nginx) to `localhost:8080`.
Depending on you configuration, you might also need to tell Telegram your certificate.

## TODOs
 - The current state logging (and appending to the lists) is not very efficient and could be handled much better. However performance was never an issue in practice.
 - Some operations (e.g., `rename`) allow the user to enter unsanitized text. While I don't see a glaring security risk right away I'm sure there are some. Use with caution! The bot was designed for a small trusted user base.
