TTL_SECONDS = 180

def reusable(entry, source_hash, now):
    return entry["source_hash"] == source_hash and now - entry["checked_at"] < TTL_SECONDS
