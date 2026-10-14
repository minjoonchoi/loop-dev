import csv
import io


def render(rows, settings):
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=settings["delimiter"], lineterminator="\n")
    columns = settings["columns"]
    if settings["include_header"]:
        writer.writerow(columns)
    for row in rows:
        writer.writerow([row[column] for column in columns])
    return output.getvalue()
