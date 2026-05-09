# live-server

## Overview

A simple, GUI-based live server for web development. 
It's written in Python for cross-platform compatibility, 
and auto-reloads the web page as it's modified, allowing 
changes to the page to be reflected immediately. It's a 
stand-alone application, so it can be used independently of an IDE. 

## Requirements
- An operating system that supports Python versions >= 3.10

## Installation

1. Install a supported Python version
2. Create a virtual environment using `venv`, or `uv`, etc.
3. Install `watchdog` and `bs4`
4. Download `server.py`, and place it in the same folder as the virtual environment

## Usage

1. Start the virtual environment
2. Run `python server.py` or `python3 server.py` in the terminal
3. Choose an HTML file and then start the server
4. Defaults to serving web pages at 'http://localhost:8000'

