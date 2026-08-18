"""
Turns a text/spreadsheet file attached to the last user message into plain
text, inlined into that message's `text` field, before any provider sees
it — so no provider file needs to know attachments exist beyond `image`
(which stays provider-specific multimodal handling, untouched). Called
once from agent.py's run_agent(), the same single choke point already
used for tools_for_mode() and the subagent pass.
"""

import base64
import io
import unicodedata
from typing import Any

import openpyxl

from server.types import ChatRequestMessage

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAX_ROWS = 300
# CSV needs no special handling — it's already plain text, so it rides the
# _decode_text() branch below like .txt does (the client sends it as
# "text/csv", which just isn't XLSX_MEDIA_TYPE).


# Excel on Windows saves "CSV" in the system ANSI codepage rather than UTF-8 —
# cp1258 on a Vietnamese locale, cp1252 on a Western one — and "Unicode Text"
# saves UTF-16. Strict UTF-8 is tried first because valid UTF-8 is effectively
# never a coincidence; a lossy replace is the last resort, not the default.
# Assuming UTF-8 here is what turned every accented character in an attached
# Vietnamese CSV into a U+FFFD box by the time it reached the chart.
# ponytail: fixed 3-codec ladder, not a detector. Add charset-normalizer only
# if a real file shows up that this misreads (neither it nor chardet is
# installed today, and a dependency for three `try` blocks isn't worth it).
_TEXT_CODECS = ("utf-8-sig", "cp1258", "cp1252")


def _decode_text(data: str) -> str:
    """
    Input: base64-encoded text/plain or text/csv file data. Output: the
    decoded string, NFC-normalized (cp1258 cannot encode precomposed ỏ/ố at
    all — it stores base letter + combining tone mark — so its output is
    decomposed until normalized).
    """
    raw = base64.b64decode(data)
    # A bare raw.decode("utf-16") with no BOM guesses little-endian and
    # silently succeeds on ordinary Latin text, so it can't join the ladder.
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return unicodedata.normalize("NFC", raw.decode("utf-16"))
    for codec in _TEXT_CODECS:
        try:
            return unicodedata.normalize("NFC", raw.decode(codec))
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _fmt_row(row: tuple[Any, ...]) -> str:
    """Input: one sheet row. Output: it as a markdown table row."""
    return "| " + " | ".join("" if cell is None else str(cell) for cell in row) + " |"


def _rows_to_markdown(header: tuple[Any, ...], rows: list[tuple[Any, ...]]) -> str:
    """
    Input: a header row and the data rows to render under it.
    Output: a markdown table. Shared by the prompt copy below and by
    sheet_row_windows() so both always render a sheet the same way.
    """
    lines = [_fmt_row(header), "| " + " | ".join("---" for _ in header) + " |"]
    lines.extend(_fmt_row(row) for row in rows)
    return "\n".join(lines)


def _decode_xlsx(data: str) -> str:
    """
    Input: base64-encoded .xlsx file data. Output: the first sheet as a
    markdown table, capped at MAX_ROWS data rows (plus a truncation note
    if cut) — LLMs read markdown tables reliably and it's far more compact
    than raw XML.
    """
    workbook = openpyxl.load_workbook(io.BytesIO(base64.b64decode(data)), data_only=True, read_only=True)
    sheet = workbook.worksheets[0]

    rows = sheet.iter_rows(values_only=True)
    header = next(rows, None)
    if header is None:
        return "(empty sheet)"

    kept: list[tuple[Any, ...]] = []
    truncated = False
    for i, row in enumerate(rows):
        if i >= MAX_ROWS:
            truncated = True
            break
        kept.append(row)

    table = _rows_to_markdown(header, kept)
    if truncated:
        table += f"\n\n(truncated to the first {MAX_ROWS} rows of sheet '{sheet.title}')"
    return table


def sheet_row_windows(data: str, media_type: str, window: int = 100, overlap: int = 10) -> list[str]:
    """
    Input: the same base64 attachment data the functions above take, plus how
    many rows go in a chunk and how many repeat between chunks.
    Output: the sheet as a list of markdown tables covering *every* row, each
    one repeating the header so a chunk read on its own still has column names.

    Deliberately not capped at MAX_ROWS. That cap is a *prompt* budget — it
    exists so an attachment can't crowd out a conversation — and it has no
    reason to bind what gets remembered. A sheet inlined for the prompt stops
    at row 300; if memory inherited that, a later question about row 900 would
    be permanently unanswerable even though the file was right there. The
    overlap keeps a row's neighbours in at least one chunk with it, so a value
    near a boundary isn't stranded away from its context.
    """
    if media_type != XLSX_MEDIA_TYPE:
        # CSV and plain text are already text; chunk them by line, treating the
        # first line as the header for the same reason as above.
        lines = _decode_text(data).splitlines()
        if not lines:
            return []
        header, rest = lines[0], lines[1:]
        if not rest:
            return [header]
        step = max(1, window - overlap)
        return ["\n".join([header, *rest[i : i + window]]) for i in range(0, len(rest), step)]

    workbook = openpyxl.load_workbook(io.BytesIO(base64.b64decode(data)), data_only=True, read_only=True)
    sheet = workbook.worksheets[0]
    rows = sheet.iter_rows(values_only=True)
    header = next(rows, None)
    if header is None:
        return []

    body = list(rows)
    if not body:
        return [_rows_to_markdown(header, [])]
    step = max(1, window - overlap)
    return [_rows_to_markdown(header, body[i : i + window]) for i in range(0, len(body), step)]


def inline_file_attachment(messages: list[ChatRequestMessage]) -> list[ChatRequestMessage]:
    """
    Input: the full chat history. Output: a new list where, if the last
    message carries a `file`, its parsed content is prepended to that
    message's text and the `file` field is dropped — everything downstream
    (all three providers) only ever sees plain text.
    """
    if not messages or messages[-1].file is None:
        return messages

    file = messages[-1].file
    if file.media_type == XLSX_MEDIA_TYPE:
        content = _decode_xlsx(file.data)
    else:
        content = _decode_text(file.data)

    last = messages[-1]
    prefixed_text = f"Attached file `{file.name}`:\n\n{content}\n\n---\n\n{last.text}"
    return [*messages[:-1], last.model_copy(update={"text": prefixed_text, "file": None})]


def _demo() -> None:
    """Self-check, run with `python -m server.attachments`."""
    assert _decode_text(base64.b64encode(b"hello world").decode()) == "hello world"

    # Every encoding Excel actually writes must survive. The last one is real
    # cp1258: ô is precomposed (0xf4), ỏ is o + a combining hook-above (0xd2).
    vi = "không hỏng"
    for raw in (vi.encode("utf-8"), vi.encode("utf-8-sig"), vi.encode("utf-16"), b"kh\xf4ng ho\xd2ng"):
        assert _decode_text(base64.b64encode(raw).decode()) == vi, raw
    assert _decode_text(base64.b64encode(b"name,revenue\na,1\n").decode()) == "name,revenue\na,1\n"

    wb = openpyxl.Workbook()
    ws = wb.active
    if ws is None:
        raise RuntimeError("Workbook did not create an active sheet")
    ws.append(["name", "revenue"])
    for i in range(305):
        ws.append([f"row{i}", i])
    buf = io.BytesIO()
    wb.save(buf)
    xlsx_b64 = base64.b64encode(buf.getvalue()).decode()

    table = _decode_xlsx(xlsx_b64)
    assert table.startswith("| name | revenue |"), table[:60]
    assert "row0" in table and "row299" in table and "row304" not in table
    assert "truncated to the first 300 rows" in table

    # The memory copy must reach rows the prompt copy truncates away, and every
    # chunk must carry the header so it stands alone.
    windows = sheet_row_windows(xlsx_b64, XLSX_MEDIA_TYPE, window=100, overlap=10)
    assert len(windows) > 1, "305 rows at window=100 must produce several chunks"
    assert all(w.startswith("| name | revenue |") for w in windows)
    joined = "\n".join(windows)
    assert "row304" in joined, "windows must cover rows past MAX_ROWS"
    assert "row0" in joined and "row150" in joined

    csv_b64 = base64.b64encode(b"name,revenue\na,1\nb,2\nc,3\n").decode()
    csv_windows = sheet_row_windows(csv_b64, "text/csv", window=2, overlap=1)
    assert all(w.startswith("name,revenue") for w in csv_windows)
    assert "c,3" in "\n".join(csv_windows)

    out = inline_file_attachment(
        [ChatRequestMessage(role="user", text="summarize this", file=None)]
    )
    assert out[0].text == "summarize this", "no-file passthrough must be a no-op"

    from server.types import ChatFileInput

    out2 = inline_file_attachment(
        [
            ChatRequestMessage(
                role="user",
                text="summarize this",
                file=ChatFileInput(name="data.xlsx", mediaType=XLSX_MEDIA_TYPE, data=xlsx_b64),
            )
        ]
    )
    assert out2[0].file is None
    assert out2[0].text.startswith("Attached file `data.xlsx`:")
    assert out2[0].text.endswith("summarize this")

    print("attachments: all checks passed")


if __name__ == "__main__":
    _demo()
