def decide(status, attempt, settings):
    retry = status in settings["retry_statuses"] and 1 <= attempt < settings["max_attempts"]
    return {"retry": retry, "delay_seconds": settings["delay_seconds"] if retry else 0}
