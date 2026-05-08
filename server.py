
import asyncio
import threading
import traceback
from bs4 import BeautifulSoup, Doctype
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from io import BytesIO, BufferedReader
from pathlib import Path
from socket import socket, AF_INET, SOCK_STREAM
from tkinter import Tk, filedialog, StringVar
from tkinter.ttk import Button, Entry, Label

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from websockets.asyncio.server import ServerConnection, serve

# For overridden send_head() from SimpleHTTPRequestHandler
import os
import urllib.parse
from http import HTTPStatus

DEFAULT_HTTP_SERVER_PORT = 8000
DEFAULT_WEBSOCKET_SERVER_PORT = 8001
HTTP_SERVER_HOSTNAME = "localhost"
WEBSOCKET_SERVER_HOSTNAME = "localhost"

LIVE_RELOAD_JS = """
	const websocket = new WebSocket("ws://%s:%s/");
	websocket.addEventListener("message", (event) => {		
		console.log(event.data);
		if (event.data == 'reload') {
			window.location.reload();
		}
	});

"""

http_server_thread = None
http_server = None
websocket_server_thread = None
websocket_server = None
file_observer_thread = None
current_http_server_port = DEFAULT_HTTP_SERVER_PORT
current_websocket_server_port = DEFAULT_WEBSOCKET_SERVER_PORT
target_file = None
target_directory = None

websocket_shutdown_event = threading.Event()

file_created_event = threading.Event()
file_modified_event = threading.Event()
file_moved_event = threading.Event()
file_deleted_event = threading.Event()


class LiveServerFileHandler(FileSystemEventHandler):

	def on_modified(self, _):
		file_modified_event.set()
	def on_deleted(self, _):
		file_deleted_event.set()
	def on_created(self, _):
		file_created_event.set()
	def on_moved(self, _):
		file_moved_event.set()

class LiveServerHTTPHandler(SimpleHTTPRequestHandler):
	def __init__(self, request, client_address, server, *, directory = None, index_page = None):
		self.index_page = index_page
		super().__init__(request, client_address, server, directory=directory)

	def send_head(self):
		
		"""Overrides the default send_head()
		
		Allows for injection of the Javascript necessary for live reloading,
		and uses `index_page` as the index page, as opposed to 'index.html'.
		Functionality involved in utilizing the browser cache has been removed.
		The code is otherwise the same, with original comments preserved.
		"""
		path = self.translate_path(self.path)
		f = None
		if os.path.isdir(path):
			parts = urllib.parse.urlsplit(self.path)
			if not parts.path.endswith('/'):
				# redirect browser - doing basically what apache does
				self.send_response(HTTPStatus.MOVED_PERMANENTLY)
				new_parts = (parts[0], parts[1], parts[2] + '/',
							 parts[3], parts[4])
				new_url = urllib.parse.urlunsplit(new_parts)
				self.send_header("Location", new_url)
				self.send_header("Content-Length", "0")
				self.end_headers()
				return None
			index = self.index_page
			index = os.path.join(path, index)
			if os.path.isfile(index):
				path = index
			#for index in self.index_pages:
			#	index = os.path.join(path, index)
			#	if os.path.isfile(index):
			#		path = index
			#		break
			else:
				return self.list_directory(path)
		ctype = self.guess_type(path)
		# check for trailing "/" which should return 404. See Issue17324
		# The test for this was added in test_httpserver.py
		# However, some OS platforms accept a trailingSlash as a filename
		# See discussion on python-dev and Issue34711 regarding
		# parsing and rejection of filenames with a trailing slash
		if path.endswith("/"):
			self.send_error(HTTPStatus.NOT_FOUND, "File not found")
			return None
		try:
			f = open(path, 'rb')

			# Only inject JS into HTML files
			if ctype == "text/html":
				html = f.read().decode()
				html = inject_live_reload_script(html)
				f = BufferedReader(BytesIO(html.encode()))

		except OSError:
			self.send_error(HTTPStatus.NOT_FOUND, "File not found")
			return None

		try:
			self.send_response(HTTPStatus.OK)
			self.send_header("Content-type", ctype)
			self.end_headers()
			return f
		except:
			f.close()
			raise

def check_if_port_in_use(hostname:str, port:int) -> bool:
	with socket(AF_INET, SOCK_STREAM) as s:
		return s.connect_ex((hostname, port)) == 0

def prepare_http_server(hostname:str, port:int) -> HTTPServer:
	global current_http_server_port
	global current_websocket_server_port
	global target_directory
	global target_file

	while check_if_port_in_use(hostname, port):
			port += 1
	current_http_server_port = port
	current_websocket_server_port = port+1

	print(current_http_server_port, current_websocket_server_port)
	handler = partial(LiveServerHTTPHandler, directory=target_directory, index_page=target_file.get())
	return HTTPServer((hostname, port), handler)

def run_http_server(hostname:str) -> bool:
	global http_server
	try:
		print(f"serving at {hostname}:{current_http_server_port}")
		http_server.serve_forever()
	except Exception:
		traceback.print_exc()
		http_server.server_close()
		return False
	http_server.server_close()
	return True

def start_http_server():
	global http_server_thread
	global http_server
	if http_server is None:
		http_server = prepare_http_server(HTTP_SERVER_HOSTNAME, DEFAULT_HTTP_SERVER_PORT)
		http_server_thread = threading.Thread(target=run_http_server, args=(HTTP_SERVER_HOSTNAME,))
		http_server_thread.start()
		print("http server started")
		start_websocket_server()
		print("websocket server started")
		start_monitoring_files()
		print("file observer started")
	else:
		print("http server already started")

def stop_http_server():
	global http_server
	global http_server_thread
	global file_observer_thread
	global websocket_server
	global websocket_server_thread

	if http_server is not None:
		http_server.shutdown()
		http_server_thread.join()
		print("http server stopped")
		http_server = None
		del http_server_thread
		http_server_thread = None
		stop_websocket_server()
		print("websocket server stopped")
		del websocket_server_thread
		websocket_server_thread = None
		stop_monitoring_files()
		print("file observer stopped")
		del file_observer_thread
		file_observer_thread = None
	else:
		print("server is not running")

def inject_live_reload_script(html:str) -> str:
	soup = BeautifulSoup(html, 'html.parser')
	doctype = [tag for tag in soup.contents if isinstance(tag, Doctype)]
	if doctype:
		doctype = doctype[0]
	if not doctype:
		first_element = soup.find()
		if hasattr(first_element, "previous"):
			first_element.insert_before("<!DOCTYPE html>")
		else:
			soup.insert(0, "<!DOCTYPE html>")
	
	script_tag = soup.new_tag("script")
	script_tag.string = LIVE_RELOAD_JS % (WEBSOCKET_SERVER_HOSTNAME, current_websocket_server_port)
	body_tag = soup.find("body")
	html_tag = soup.find("html")
	if body_tag is not None:
		body_tag.append(script_tag)
	elif html is not None:
		html_tag.append(script_tag)
	else:
		pass

	return soup.prettify()

def start_websocket_server():
	global websocket_server
	global websocket_server_thread

	if websocket_server_thread is None:
		websocket_server_thread = threading.Thread(target=asyncio.run, args=(run_websocket_server(),))
		websocket_server_thread.start()
	else:
		print("websocket server is already running")

def stop_websocket_server():
	global websocket_server
	global websocket_server_thread
	global websocket_shutdown_event

	if websocket_server_thread is not None:
		websocket_shutdown_event.set()
		websocket_server_thread.join()

async def run_websocket_server():
	global current_websocket_server_port
	global websocket_server
	try:
		while check_if_port_in_use(WEBSOCKET_SERVER_HOSTNAME, DEFAULT_WEBSOCKET_SERVER_PORT):
			current_websocket_server_port += 1

		websocket_server = await serve(send_websocket_message, WEBSOCKET_SERVER_HOSTNAME, current_websocket_server_port)
		while True:
			if websocket_shutdown_event.is_set():
				websocket_shutdown_event.clear()
				websocket_server.close()
				break
			await asyncio.sleep(0.01)

	except Exception:
		traceback.print_exc()

async def send_websocket_message(websocket:ServerConnection):
	message = None
	try:
		while True:
			if file_modified_event.is_set():
				file_modified_event.clear()
				message = "reload"
			elif file_created_event.is_set():
				file_created_event.clear()
				message = "file created"
			elif file_moved_event.is_set():
				file_moved_event.clear()
				message = "file moved"
			elif file_deleted_event.is_set():
				file_deleted_event.clear()
				message = "file deleted"
			
			if message is not None:
				await websocket.send(message)
				break
			else:
				pass
			await asyncio.sleep(0.1)
	except Exception:
		traceback.print_exc()

def start_monitoring_files():
	global file_observer_thread

	event_handler = LiveServerFileHandler()
	if file_observer_thread is None and target_file is not None:
		file_observer_thread = Observer()
		file_observer_thread.schedule(event_handler, Path(target_file.get()).parent, recursive=False)
		file_observer_thread.start()
	elif target_file is None:
		print("no target file was selected")
	else:
		print("file observer thread is already running")

def stop_monitoring_files():
	global file_observer_thread

	if file_observer_thread is not None:
		file_observer_thread.stop()
		file_observer_thread.join()
	else:
		print("file observer thread is not running")

def choose_target_file():
	global target_file
	global target_directory

	target_file.set(filedialog.askopenfilename())
	print(target_file.get())
	
	target_directory = Path(target_file.get()).parent

def check_events():
	global http_server_thread
	global start_button
	global stop_button
	global select_file_button

	if http_server_thread and http_server_thread.is_alive():
		start_button.configure(state='disabled')
		stop_button.configure(state='enabled')
		select_file_button.configure(state='disabled')
	else:
		start_button.configure(state='enabled')
		stop_button.configure(state='disabled')
		select_file_button.configure(state='enabled')
	
	window.after(100, check_events)

def main():

	global window
	window = Tk()
	window.title("Live Server")
	window.geometry("640x360")
	window.resizable(width=False, height=False)

	total_rows = 12
	total_cols = 16
	for row in range(0,total_rows):
		window.grid_rowconfigure(row, weight=1, uniform="a")
	for col in range(0, total_cols):
		window.grid_columnconfigure(col, weight=1, uniform="a")

	global start_button
	start_button = Button(window, text="Start", command=start_http_server)
	start_button.grid(column=5, columnspan=2, row=8)

	global stop_button
	stop_button = Button(window, text="Stop", command=stop_http_server)
	stop_button.grid(column=7, columnspan=2, row=8)

	global target_file
	target_file = StringVar()

	global select_file_button
	select_file_button = Button(window, text="Choose File", command=choose_target_file)
	select_file_button.grid(column=9, columnspan=2, row=8)

	target_file_label = Label(window, textvariable=target_file)
	target_file_label.grid(column=1, row=2, columnspan=14)

	window.after(100, check_events)
	window.mainloop()
	
	if http_server_thread:
		stop_http_server()
		
if __name__ == "__main__":
	main()