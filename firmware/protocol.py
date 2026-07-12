import sys

try:
    import ujson as json
except ImportError:
    import json

try:
    import select
except ImportError:
    select = None


class SerialProtocol:
    def __init__(self):
        self.poller = None
        self.disconnected = False

        if select is not None:
            try:
                self.poller = select.poll()
                self.poller.register(sys.stdin, select.POLLIN)
            except Exception:
                self.poller = None

    def send(self, message):
        sys.stdout.write(json.dumps(message))
        sys.stdout.write("\n")
        try:
            sys.stdout.flush()
        except Exception:
            pass

    def read_commands(self, max_lines=8):
        if self.disconnected:
            return []

        if self.poller is None:
            return []

        commands = []

        while len(commands) < max_lines and self.poller.poll(0):
            line = sys.stdin.readline()
            if not line:
                self.disconnected = True
                commands.append({
                    "type": "disconnect",
                })
                break

            line = line.strip()
            if not line:
                continue

            try:
                commands.append(json.loads(line))
            except Exception as exc:
                self.send({
                    "type": "error",
                    "where": "serial_rx",
                    "message": str(exc),
                })

        return commands

