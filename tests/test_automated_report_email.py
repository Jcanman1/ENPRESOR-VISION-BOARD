import os
import tempfile
from EnpresorOPCDataViewBeforeRestructureLegacy import send_report_email, email_settings

class DummySMTP:
    def __init__(self, server, port):
        self.server = server
        self.port = port
        self.sent = []
    def starttls(self):
        pass
    def login(self, username, password):
        pass
    def sendmail(self, from_addr, to_addr, msg):
        self.sent.append((from_addr, to_addr))
    def quit(self):
        pass


def test_send_report_email_without_threshold(monkeypatch):
    # patch SMTP to avoid network
    monkeypatch.setattr('smtplib.SMTP', DummySMTP)
    # ensure email settings allow automated reporting but disable threshold emails
    email_settings.update({
        'email_address': 'user@example.com',
        'email_enabled': False,
        'automated_report_enabled': True,
        'smtp_server': 'smtp.test',
        'smtp_port': 25,
        'smtp_username': '',
        'smtp_password': '',
        'from_address': 'from@example.com',
    })
    # create temp file to send
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(b'data')
        path = tmp.name
    try:
        assert send_report_email(path) is True
    finally:
        os.remove(path)
