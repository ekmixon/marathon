#!/usr/bin/env python

import logging
import os
import platform
import signal
import socket
import sys
import subprocess
import re
import json

# Ensure compatibility with Python 2 and 3.
# See https://github.com/JioCloud/python-six/blob/master/six.py for details.
PY2 = sys.version_info[0] == 2
PY3 = sys.version_info[0] == 3

if PY2:
    from SimpleHTTPServer import SimpleHTTPRequestHandler
    from SocketServer import TCPServer as HTTPServer
    from urllib2 import Request, urlopen
else:
    from http.server import SimpleHTTPRequestHandler
    from http.server import HTTPServer
    from urllib.request import Request, urlopen

if PY2:
    byte_type = unicode # NOQA

    def response_status(response):
        return response.getcode()

else:
    byte_type = bytes

    def response_status(response):
        return response.getcode()


def cgroup_name(resource_type):
    logging.info("Looking for my cgroup for resource type %s", resource_type)
    with open("/proc/self/cgroup", "r") as file:
        lines = file.readlines()
        for line in lines:
            logging.info("/proc/self/cgroup: %s", line)
            [idx, resource_types, cgroup_name] = line.strip().split(":")
            for t in resource_types.split(","):
                if t == resource_type:
                    logging.info("My cgroup: %s", cgroup_name)
                    return cgroup_name


# reads all the files in a folder that are readable, return them in a map of filename: contents
def read_cgroup_values(resource_type):
    name = cgroup_name(resource_type)
    if name is None:
        return {}
    folder = os.path.join("/sys/fs/cgroup", resource_type) + name
    result = {}
    for filename in os.listdir(folder):
        path = os.path.join(folder, filename)
        print(path)
        with open(path, 'r') as file:
            try:
                result[filename] = file.read().strip()
            except IOError:
                ()
                # ignore
    return result


def make_handler(app_id, version, task_id, base_url):
    """
    Factory method that creates a handler class.
    """



    class Handler(SimpleHTTPRequestHandler):

        def handle_ping(self):
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()

            msg = f"Pong {app_id}"

            self.wfile.write(byte_type(msg, "UTF-8"))

        def check_readiness(self):

            url = f"{base_url}/{task_id}/ready"

            logging.debug("Query %s for readiness", url)
            url_req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            response = urlopen(url_req)
            res = response.read()
            status = response_status(response)
            logging.debug("Current readiness is %s, %s", res, status)

            self.send_response(status)
            self.send_header('Content-type', 'text/html')
            self.end_headers()

            self.wfile.write(res)

            logging.debug("Done processing readiness request.")
            return

        def check_health(self):

            url = f"{base_url}/health"

            logging.debug("Query %s for health", url)
            url_req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            response = urlopen(url_req)
            res = response.read()
            status = response_status(response)
            logging.debug("Current health is %s, %s", res, status)

            self.send_response(status)
            self.send_header('Content-type', 'text/html')
            self.end_headers()

            self.wfile.write(res)

            logging.debug("Done processing health request.")
            return

        # This method returns the size of the shared memory fs, mounted at /dev/shm
        # It parses the output of 'df -m /dev/shm', extracts the "Size" column of the output and returns
        # that number
        def handle_ipc_shm_info(self):
            logging.debug("Reporting IPC shm info")
            df_shm_info = subprocess.check_output(["df", "-m", "/dev/shm"])

            # Example Output:
            #
            # Filesystem            Size  Used Avail Use% Mounted on
            # tmpfs                   23       0    23   0% /dev/shm
            shm_size = re.search(
                'tmpfs\\s+([0-9]+)\\s+[0-9]+\\s+[0-9]+\\s+[0-9]+%\\s+/dev/shm',
                df_shm_info,
            )[1]


            self.send_response(200)
            self.send_header('Content-Type', 'application/text')
            self.end_headers()

            self.wfile.write(shm_size)

            logging.debug("Done reporting IPC shm info.")
            return

        # This method gathers the IPC namespace ID and returns it to the caller. Can be used to make sure
        # two processes access the same shared memory segments
        def handle_ipc_ns_info(self):
            logging.debug("Reporting IPC namespace info")
            ipc_ns_info = subprocess.check_output(["stat", "-Lc", "%i", "/proc/self/ns/ipc"])

            self.send_response(200)
            self.send_header('Content-Type', 'application/text')
            self.end_headers()

            self.wfile.write(ipc_ns_info)

            logging.debug("Done reporting IPC ns info.")
            return

        def handle_cgroup_info(self):
            cgroup_info = {
                "memory": read_cgroup_values("memory"),
                "cpu": read_cgroup_values("cpu")
            }
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()

            self.wfile.write(json.dumps(cgroup_info))

            logging.debug("Done reporting cgroup info.")
            return

        def handle_suicide(self):

            logging.info("Received a suicide request. Sending a SIGTERM to myself.")
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()

            os.kill(os.getpid(), signal.SIGTERM)
            return

        def do_GET(self):
            try:
                logging.debug(f"Got GET request for path {self.path}")
                if self.path == '/ping':
                    return self.handle_ping()
                elif self.path == '/ready':
                    return self.check_readiness()
                elif self.path == '/health':
                    return self.check_health()
                elif self.path == '/ipcshm':
                    return self.handle_ipc_shm_info()
                elif self.path == '/ipcns':
                    return self.handle_ipc_ns_info()
                elif self.path == "/cgroup":
                    return self.handle_cgroup_info()
                else:
                    return SimpleHTTPRequestHandler.do_GET(self)
            except Exception:
                logging.exception(f"Could not handle GET request for path {self.path}")

        def do_POST(self):
            try:
                logging.debug(f"Got POST request for path {self.path}")
                return self.check_health()
            except Exception:
                logging.exception(f"Could not handle POST request for path {self.path}")

        def do_DELETE(self):
            try:
                logging.debug(f"Got DELETE request for path {self.path}")
                if self.path == '/suicide':
                    return self.handle_suicide()
            except Exception:
                logging.exception(f"Could not handle DELETE request for path {self.path}")


    return Handler


if __name__ == "__main__":
    logging.basicConfig(
        format='%(asctime)s %(levelname)-8s: %(message)s',
        level=logging.DEBUG)
    logging.info(platform.python_version())
    logging.debug(sys.argv)

    port = int(sys.argv[1])
    app_id = sys.argv[2]
    version = sys.argv[3]
    base_url = sys.argv[4]
    task_id = os.getenv("MESOS_TASK_ID", "<UNKNOWN>")

    # Defer binding and activating the server to a later point, allowing to set
    # allow_reuse_address=True option.
    httpd = HTTPServer(("", port),
                       make_handler(app_id, version, task_id, base_url),
                       bind_and_activate=False)
    httpd.allow_reuse_address = True

    msg = "AppMock[%s %s]: %s has taken the stage at port %d. "\
          "Will query %s for health and readiness status."
    logging.info(msg, app_id, version, task_id, port, base_url)

    # Trigger proper shutdown on SIGTERM.
    def handle_sigterm(signum, frame):
        logging.warning(f"Received {signum} signal. Closing the server...")
        httpd.server_close()

    signal.signal(signal.SIGTERM, handle_sigterm)

    try:
        httpd.server_bind()
        httpd.server_activate()
        httpd.serve_forever()
    except socket.error as e:
        # If "[Errno 48] Address already in use" then grep for the process using the port
        if e.errno == 48:
            logging.error("Failed to bind to port %d. Trying to grep blocking process:", port)
            os.system("ps -a | grep $(lsof -ti :{})".format(port))
        else:
            logging.exception("Socket.error in the main thread: ")
    except Exception:
        logging.exception("Exception in the main thread: ")
    finally:
        logging.info("Closing the server...")
        httpd.server_close()
