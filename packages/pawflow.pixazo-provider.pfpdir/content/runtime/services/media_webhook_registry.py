from pawflow import pfp


class _Ticket:
    def __init__(self, data):
        self.token = str(data.get("token") or "")
        self.url = str(data.get("url") or "")

    def wait(self, timeout, cancel_event=None, poll_interval=1):
        return pfp._host_call(
            "media_webhook", "", operation="wait",
            arguments={"token": self.token, "timeout": timeout,
                       "poll_interval": poll_interval})

    def poll(self):
        return pfp._host_call(
            "media_webhook", "", operation="poll",
            arguments={"token": self.token})

    def close(self):
        pfp._host_call(
            "media_webhook", "", operation="close",
            arguments={"token": self.token})


class MediaWebhookRegistry:
    @classmethod
    def instance(cls):
        return cls()

    def register(self, provider, base_url):
        data = pfp._host_call(
            "media_webhook", "", operation="register",
            arguments={"provider": provider, "base_url": base_url})
        return _Ticket(data)
