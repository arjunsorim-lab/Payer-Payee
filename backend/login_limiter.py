"""Atomic, expiring login attempt limits shared by all deployment workers."""
import hashlib


class RedisLoginLimiter:
    # The increment and TTL happen together; process crashes cannot leave
    # permanent keys, and concurrent workers cannot bypass the limit.
    SCRIPT = """
    local count = redis.call('INCR', KEYS[1])
    if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
    return count
    """

    def __init__(self, client, limit=5, window_seconds=900):
        self.client = client
        self.limit = limit
        self.window_seconds = window_seconds

    @classmethod
    def from_url(cls, url):
        import redis
        return cls(redis.Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2))

    def allow(self, address):
        key = "payerpayee:login:" + hashlib.sha256(address.encode()).hexdigest()
        return int(self.client.eval(self.SCRIPT, 1, key, self.window_seconds)) <= self.limit
