.DEFAULT_GOAL := help
PYTHON ?= python3
BOT_PYTHON := .venv/bin/python

.PHONY: help setup setup-realms realms-login start stop run preview emoji-prepare
help:
	@printf '%s\n' 'make setup        Install local bot dependencies' \
	  'make setup-realms Install additional dependencies for Realms authentication' \
	  'make realms-login Authorize the Realms account without connecting to Telegram' \
	  'make start        Start the Telethon bot in the background' \
	  'make stop         Stop the Telethon bot gracefully' \
	  'make run          Run the Telethon bot in the current terminal' \
	  'make preview      Read the game statuses without connecting to Telegram' \
	  'make emoji-prepare Convert game icons and player heads into local emoji PNGs'

setup:
	$(PYTHON) -m venv .venv
	$(BOT_PYTHON) -m pip install -r requirements-local.txt

setup-realms: setup
	$(BOT_PYTHON) -m pip install 'SQLAlchemy>=2,<3' 'PyMySQL[rsa]>=1.2,<2'

realms-login:
	$(BOT_PYTHON) -m realm_status --realms-login

run:
	$(BOT_PYTHON) -m realm_status

start:
	$(BOT_PYTHON) -m realm_status.lifecycle start

stop:
	$(BOT_PYTHON) -m realm_status.lifecycle stop

preview:
	$(BOT_PYTHON) -m realm_status --preview

emoji-prepare:
	$(BOT_PYTHON) -m realm_status --emoji-prepare
