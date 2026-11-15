"""Kaagaz on WhatsApp's uploads (#603 W5, PRD 7 section 15 "Upload a file", "Choosing a folder"):
a file or photos the person sends are saved, after a tap, into a folder they can see, through the
browser's own upload. Everything runs as the person (``db.user`` only), through PRD 7's views.

* **Waiting, never saved without a tap.** The files the person sent one after another (the channel
  waits QUIET_SECONDS after the last, kaagaz/channel/loop.py) are kept in the person's
  ``whatsapp_pickers`` row (the one "Which file?" uses too) for PICKER_MINUTES: Meta's ids and
  their names only; nothing is fetched or stored before the tap. Then: 'Save "Invoice.pdf" to
  Kaagaz?' [Choose folder] [Let Kaagaz decide] [Cancel]; several photos first ask [One document]
  [Separate files] [Cancel]. More files sent before the tap join them. The first time, the bot adds
  its tip (send scans as Document). An old tap once they expired gets NOTHING_WAITING.
* **The folder browser.** Only folders the person sees (``visible_folders``, db/whatsapp_upload.py):
  their last 3 (re-checked), then the top level (a folder whose parent they can't see is at the
  top, as in the browser), MAX_ROWS rows a list, "More folders" for the next ones, "Type a folder
  name" and "Let Kaagaz decide" (when auto-filing applies to them, ``autofile.applies``). A row
  shows the name, and the path only when every folder in it is one they see, with how many they
  see inside. WhatsApp sees only short random row ids, each list's own (``gen``); a tap is looked
  up in the person's row. A folder with folders inside gives [Save here] [Open folder] [Back], one
  with none [Save here] [Back]; [Open folder] lists its folders the same way, as deep as it goes.
* **Every tap is checked again**, as the person, just before it is used: a folder hidden or deleted
  since, or a row id that never was, gets FOLDER_GONE (the same words, so nothing hints it exists)
  and a fresh list; a tap from an older list gets LIST_CHANGED and a fresh list.
* **Saving** is ``POST /documents``'s own (``documents.store_upload``): the folder checked again
  as the person before anything is stored (before the tree or family lock), the type, size and
  storage limits, duplicates and PRD 7's three answers, the batch cap and the virus scan job, the
  audit ``via: whatsapp`` with the person as subject. Each file is fetched from Meta just before
  (``MetaWhatsApp.media``, its size checked against the largest limit first, then ``download``
  streamed into the upload) and leaves the waiting files once it is saved or refused, so a retry
  goes on with the rest. A folder hidden partway stops the batch: the rest wait for a new choice.
  All the files of one tap are one batch (the browser's batch tray). "One document" joins the
  photos into one PDF first (kaagaz/channel/photos.py, nothing decoded); photos it can't join are
  saved one by one, and the reply says so.
* **The reply** names each file: 'Saved "Invoice.pdf" in (the folder's path).' with an Open in
  Kaagaz link (only its id), or the duplicate's words (section 5: its name and link; in Trash; a
  hidden copy's HIDDEN_COPY; a locked copy, never named), or why it was refused. The file itself is
  never sent back. Names and paths are masked (``texts.mask``); links are added after.
* **Notices** (``notice``): each new file's message waits again (``await_notice``) until Kaagaz has
  read the file: "Ready" with where it is filed, failed the virus check, or couldn't be read. A file
  hidden, locked or deleted since gets no notice; ``Chat.recheck`` checks again just before it goes.
* **Switched off**: the Admin's "Allow saving files from WhatsApp" (W3) off gets UPLOADS_OFF, at
  the start, when files come and at the tap. **Limits**: MAX_FILES_AN_HOUR files sent a person, in
  the database (``files_this_hour``), past which they get TOO_MANY_FILES.
"""

import http.client
import secrets
import tempfile
import uuid
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import UploadFile

from kaagaz.adapters.whatsapp import MetaWhatsApp, WhatsAppError
from kaagaz.api.auth import CurrentUser
from kaagaz.api.context import AppContext
from kaagaz.api.errors import ApiError
from kaagaz.api.routers import documents
from kaagaz.channel.chat import DESCRIPTION_CHARS, MAX_BODY, MAX_ROWS, ROW_CHARS, _cut, _lang, _masked
from kaagaz.channel.outgoing import Outgoing, Row
from kaagaz.channel.photos import PhotosPdf
from kaagaz.channel.texts import Language, say
from kaagaz.db import autofile, listing
from kaagaz.db import whatsapp as wa
from kaagaz.db import whatsapp_chat as wa_chat
from kaagaz.db import whatsapp_upload as wa_up
from kaagaz.db.whatsapp import Linked, Waiting
from kaagaz.ingestion import sniff
from kaagaz.ingestion.policy import UploadPolicy

PICKER_MINUTES = 15  # the files and the folder list wait this long for a tap
MAX_FILES_AN_HOUR = 30  # files a person may send in an hour (shortcut: a guess; no setting yet)
NAME_CHARS = 80  # a file's name in a reply
PHOTO_BYTES = 16 * 1024 * 1024  # a photo held in memory while the PDF is written (Meta's are 5 MB)
TAP = "up"  # every upload button and row id starts "up:"
REFUSED = {
    "too_large": "too_large",
    "unsupported_type": "wrong_type",
    "empty_file": "empty_file",
    "storage_full": "storage_full",
}


class _MediaGone(Exception):
    """Meta no longer has the file (its 7 days are over), or never had it."""


class Upload:
    def __init__(self, context: AppContext) -> None:
        self.context = context
        self._meta: MetaWhatsApp | None = None

    # --- the way in (from Chat) ------------------------------------------------------------

    def start(self, user: CurrentUser, lang: Language) -> Outgoing:
        """[Upload a file]: "Send the file or photos now", the tip the first time."""
        with self.context.db.user(user.customer_id, user.user_id) as conn:
            if not self._on(conn, user):
                return Outgoing(say("uploads_off", lang), lang=lang)
            first = wa_up.files_kept(conn, user.customer_id, user.user_id) == 0
            self._save(conn, user, {"step": "upload", "lang": lang, "files": []})
        tip = "\n" + say("tip", lang) if first else ""
        return Outgoing(say("send_now", lang) + tip, lang=lang)

    def files(self, user: CurrentUser, linked: Linked, message: Waiting, lang: Language) -> Outgoing:
        """The files the person sent one after another, from ``message`` on: kept waiting for a
        tap (joining any already waiting), and the question how to save them."""
        c = user.customer_id
        with self.context.db.user(c, user.user_id) as conn:
            sent, _ = wa_up.run(conn, c, linked.link_id)
            done = tuple(each.id for each in sent if each.id != message.id)
            state = self._state(conn, user, waiting_only=False)
            lang = _lang(str(state.get("lang"))) if state else lang
            if not self._on(conn, user):
                wa_chat.clear_step(conn, c, user.user_id)
                return Outgoing(say("uploads_off", lang), lang=lang, finished=done)
            if wa_up.files_this_hour(conn, c, user.user_id) > MAX_FILES_AN_HOUR:
                return Outgoing(say("too_many_files", lang), lang=lang, finished=done)
            first = wa_up.files_kept(conn, c, user.user_id) == len(sent)
            waiting = list(state.get("files") or []) if state else []
            stamp = self._stamp(sent[0].sent_at) if sent else ""
            photos = [each for each in sent if each.kind == "image"]
            for each in sent:
                if each.media_id is None:
                    continue
                name = each.name or "document"
                if each.kind == "image":
                    number = f" ({photos.index(each) + 1})" if len(photos) > 1 else ""
                    name = f"WhatsApp photo {stamp}{number}"
                waiting.append({"m": each.id, "media": each.media_id, "name": name, "kind": each.kind})
            if not waiting:
                return Outgoing(say("nothing_waiting", lang), lang=lang, finished=done)
            auto = autofile.applies(conn, c, user.user_id)
            self._save(conn, user, {"step": "upload", "lang": lang, "files": waiting, "gen": 0})
        tip = "\n" + say("tip", lang) if first and photos else ""
        cancel = (f"{TAP}:cancel::{lang}", say("cancel_button", lang))
        if len(waiting) > 1 and all(each["kind"] == "image" for each in waiting):
            one = (f"{TAP}:one::{lang}", say("one_button", lang))
            apart = (f"{TAP}:separate::{lang}", say("separate_button", lang))
            return Outgoing(
                say("photos", lang, len(waiting)) + tip, (one, apart, cancel), lang=lang, finished=done
            )
        question = (
            say("save_one", lang, self._name(waiting[0]["name"]))
            if len(waiting) == 1
            else say("save_many", lang, len(waiting))
        )
        buttons = [(f"{TAP}:choose::{lang}", say("choose_button", lang))]
        buttons += [(f"{TAP}:auto::{lang}", say("auto_button", lang))] if auto else []
        return Outgoing(question + tip, (*buttons, cancel), lang=lang, finished=done)

    def tapped(self, user: CurrentUser, linked: Linked, tapped: str) -> Outgoing:
        """A button or row of an upload ("up:<action>:<list>:<row>:<language>")."""
        _, action, gen, row, lang_code = [*tapped.split(":"), "", "", "", ""][:5]
        lang = _lang(lang_code)
        with self.context.db.user(user.customer_id, user.user_id) as conn:
            state = self._state(conn, user)
            if state is None:
                return Outgoing(say("nothing_waiting", lang), lang=lang)
            lang = _lang(str(state.get("lang")))
            if action == "cancel":
                wa_chat.clear_step(conn, user.customer_id, user.user_id)
                return Outgoing(say("cancelled", lang), self._menu(lang), lang=lang)
            if not self._on(conn, user):  # switched off since
                wa_chat.clear_step(conn, user.customer_id, user.user_id)
                return Outgoing(say("uploads_off", lang), lang=lang)
        if action in ("one", "separate"):
            return self._list(user, linked, {**state, "one": action == "one"}, None, 0)
        if action == "choose":
            return self._list(user, linked, state, None, 0)
        if action == "auto":
            return self._file(user, linked, state, None)
        at, skip = state.get("at"), int(state.get("skip") or 0)
        if gen != str(state.get("gen")):  # a list older than the last one sent
            return self._list(user, linked, state, at, skip, lead=say("list_changed", lang))
        if action == "more":
            return self._list(user, linked, state, at, int(state.get("next") or 0))
        if action == "find":
            with self.context.db.user(user.customer_id, user.user_id) as conn:
                self._save(conn, user, {**state, "typing": True})
            return Outgoing(say("type_folder", lang), lang=lang)
        if action == "up":
            return self._list(user, linked, state, self._above(user, at), 0)
        picked = state.get("rows", {}).get(row) if action == "f" else state.get("picked")
        with self.context.db.user(user.customer_id, user.user_id) as conn:  # just before it is used
            seen = wa_up.about(conn, user.customer_id, [picked] if isinstance(picked, str) else [])
        if not isinstance(picked, str) or picked not in seen:  # hidden, deleted or never was: alike
            return self._list(user, linked, state, at, skip, lead=say("folder_gone", lang))
        if action == "f":
            return self._folder(user, state, picked, seen[picked])
        if action == "save":
            return self._file(user, linked, state, picked)
        if action == "open":
            return self._list(user, linked, state, picked, 0)
        return self._list(user, linked, state, at, skip)  # [Back]: the list it was picked from

    def typed(self, user: CurrentUser, linked: Linked, words: str) -> Outgoing | None:
        """A folder's name typed after "Type a folder name"; None when no upload waits for one."""
        with self.context.db.user(user.customer_id, user.user_id) as conn:
            state = self._state(conn, user)
        if state is None or not state.get("typing"):
            return None
        return self._list(user, linked, {**state, "typing": False}, state.get("at"), 0, named=words)

    def notice(self, user: CurrentUser, message: Waiting) -> Outgoing | None:
        """A saved file's notice once Kaagaz has read it; None while it is being read; an empty
        body when the file is hidden, locked or deleted since (nothing goes)."""
        lang, document_id = _lang(str(message.body)), str(message.document_id)
        quiet = Outgoing("", lang=lang, notice=True)
        with self.context.db.user(user.customer_id, user.user_id) as conn:
            row = listing.get_document(conn, user.customer_id, document_id)
            if row is None or not wa_chat.on_whatsapp(conn, user.customer_id, [document_id]):
                return quiet
            if row["status"] in ("received", "clean"):
                return None
            folder = None if row["folder_id"] is None else str(row["folder_id"])
            place = wa_up.about(conn, user.customer_id, [folder] if folder else []).get(folder or "")
        name = self._name(str(row["display_name"]))
        link = say("open_in_kaagaz", lang, self._url(f"/documents/{document_id}"))
        if row["status"] in ("ready", "needs_reader"):
            where = None if place is None else _masked(place.path or place.name)
            said = say("ready", lang, name, where) if where else say("ready_unfiled", lang, name)
            text = f"{said}\n{link}"
        elif row["status"] == "rejected":
            text = say("rejected", lang, name)
        else:
            text = f"{say('not_read', lang, name)}\n{link}"
        return Outgoing(text, documents=(document_id,), lang=lang, notice=True)

    # --- the folder browser ----------------------------------------------------------------

    def _list(
        self,
        user: CurrentUser,
        linked: Linked,
        state: dict[str, Any],
        at: str | None,
        skip: int,
        *,
        lead: str = "",
        named: str | None = None,
    ) -> Outgoing:
        """One list of folders: inside ``at`` (None: the top level) from its ``skip``-th, or those
        ``named`` like some words; a new list number and row ids, kept in the person's row."""
        c, lang = user.customer_id, _lang(str(state.get("lang")))
        gen = int(state.get("gen") or 0) + 1
        with self.context.db.user(c, user.user_id) as conn:
            if at is not None and at not in wa_up.about(conn, c, [at]):  # hidden or gone since
                at, skip, lead = None, 0, say("folder_gone", lang)
            top = at is None and named is None
            recent = wa_up.recent(conn, c, linked.link_id) if top and skip == 0 else []
            seen = wa_up.about(conn, c, recent)
            recent = [each for each in recent if each in seen]
            # The top level's pages leave out the recent folders its first page shows, once each.
            shown_recent = recent if skip == 0 else [str(each) for each in state.get("recent") or []]
            without = shown_recent if top else []
            auto = top and autofile.applies(conn, c, user.user_id)
            room = MAX_ROWS - len(recent) - 1 - int(auto) - int(at is not None) - 1  # the rest; More
            if named is not None:
                ids = wa_up.named(conn, c, named.strip()[:100], room + 1)
            else:
                ids = wa_up.level(conn, c, at, skip, room + 1, without=without)
            more, ids = named is None and len(ids) > room, ids[:room]
            seen |= wa_up.about(conn, c, [*ids, *([at] if at else [])])
            rows: dict[str, str] = {}
            listed: list[Row] = []
            for each, recently in [*((r, True) for r in recent), *((i, False) for i in ids)]:
                token = secrets.token_hex(4)
                rows[token] = each
                listed.append(self._row(f"{TAP}:f:{gen}:{token}:{lang}", seen[each], recently, lang))
            if more:
                listed.append(Row(f"{TAP}:more:{gen}::{lang}", say("more_folders_row", lang)))
            if at is not None:
                listed.append(Row(f"{TAP}:up:{gen}::{lang}", say("back_button", lang)))
            listed.append(Row(f"{TAP}:find:{gen}::{lang}", say("type_name_row", lang)))
            if auto:
                listed.append(Row(f"{TAP}:auto::{lang}", say("auto_button", lang)))
            kept = {**state, "gen": gen, "rows": rows, "at": at, "skip": skip, "next": skip + len(ids)}
            kept["recent"] = without
            self._save(conn, user, {**kept, "picked": None, "typing": False})
        files = state.get("files") or []
        if named is not None:
            heading = say("found_folders", lang) if ids else say("no_folder_named", lang)
        elif at is not None:
            heading = say("folders_in", lang, _masked(seen[at].path or seen[at].name))
        elif len(files) == 1:
            heading = say("pick_folder", lang, self._name(files[0]["name"]))
        else:
            heading = say("pick_folder_many", lang, len(files))
        body = f"{lead}\n{heading}" if lead else heading
        return Outgoing(body, rows=tuple(listed), button=say("choose", lang), lang=lang)

    def _row(self, row_id: str, about: wa_up.About, recently: bool, lang: Language) -> Row:
        inside = (
            ""
            if about.inside == 0
            else say("inside_one", lang)
            if about.inside == 1
            else say("inside", lang, about.inside)
        )
        parts = [say("recent", lang) if recently else "", _masked(about.path or ""), inside]
        description = " · ".join(part for part in parts if part)
        return Row(row_id, _cut(_masked(about.name), ROW_CHARS), _cut(description, DESCRIPTION_CHARS))

    def _folder(self, user: CurrentUser, state: dict[str, Any], folder: str, about: wa_up.About) -> Outgoing:
        """A folder tapped: its path, [Save here], [Open folder] when it has folders inside, [Back]."""
        lang, gen = _lang(str(state.get("lang"))), int(state.get("gen") or 0) + 1
        with self.context.db.user(user.customer_id, user.user_id) as conn:
            self._save(conn, user, {**state, "gen": gen, "rows": {}, "picked": folder, "typing": False})
        buttons = [(f"{TAP}:save:{gen}::{lang}", say("save_here_button", lang))]
        if about.inside:
            buttons.append((f"{TAP}:open:{gen}::{lang}", say("open_folder_button", lang)))
        buttons.append((f"{TAP}:back:{gen}::{lang}", say("back_button", lang)))
        return Outgoing(_cut(_masked(about.path or about.name), MAX_BODY), tuple(buttons), lang=lang)

    def _above(self, user: CurrentUser, at: str | None) -> str | None:
        """The folder above ``at`` as the person sees the tree (None: the top)."""
        if at is None:
            return None
        with self.context.db.user(user.customer_id, user.user_id) as conn:
            found = wa_up.about(conn, user.customer_id, [at]).get(at)
        return None if found is None else found.above

    # --- saving ----------------------------------------------------------------------------

    def _file(self, user: CurrentUser, linked: Linked, state: dict[str, Any], folder: str | None) -> Outgoing:
        """The waiting files saved into ``folder`` (None: Let Kaagaz decide, the browser's upload
        with no folder), one batch, through the browser's upload; the reply naming each."""
        c, lang = user.customer_id, _lang(str(state.get("lang")))
        with self.context.db.user(c, user.user_id) as conn:
            place = None if folder is None else wa_up.about(conn, c, [folder]).get(folder)
            if folder is not None and place is None:  # checked again (and by the upload itself)
                return self._list(user, linked, state, state.get("at"), 0, lead=say("folder_gone", lang))
            if folder is None and not autofile.applies(conn, c, user.user_id):
                return self._list(user, linked, state, None, 0)
        where = None if place is None else _masked(place.path or place.name)
        policy = documents.user_upload_policy(self.context, user)
        batch = str(uuid.uuid4())
        waiting: list[dict[str, Any]] = list(state.get("files") or [])
        said: list[str] = []
        named: list[str] = []
        saved: list[tuple[str, str]] = []  # (message, document) of each new file, for its notice
        stopped = False
        if state.get("one") and len(waiting) > 1 and all(each["kind"] == "image" for each in waiting):
            name = f"{waiting[0]['name'].removesuffix(' (1)')} ({len(waiting)} pages).pdf"
            try:
                joined = self._joined(user, waiting, policy, folder, batch, name)
            except ApiError as refused:
                stopped = refused.status == 404  # the folder hidden or deleted since: they all wait
                if not stopped:
                    said.append(say(REFUSED.get(refused.code, "not_saved"), lang, self._name(name)))
                    waiting = []
            else:
                if joined is None:
                    said.append(say("not_joined", lang))
                else:
                    line, document, new = self._said(user, joined, name, where, lang)
                    said.append(line)
                    named += [document] if document else []
                    saved += [(waiting[0]["m"], document)] if new and document else []
                    waiting = []
        while waiting and not stopped:
            each = waiting[0]
            try:
                reply = self._store(user, each, policy, folder, batch)
            except ApiError as refused:
                if refused.status == 404:  # the folder hidden or deleted since: the rest wait
                    stopped = True
                    break
                said.append(say(REFUSED.get(refused.code, "not_saved"), lang, self._name(each["name"])))
            except _MediaGone:
                said.append(say("media_gone", lang, self._name(each["name"])))
            else:
                line, document, new = self._said(user, reply, each["name"], where, lang)
                said.append(line)
                named += [document] if document else []
                saved += [(each["m"], document)] if new and document else []
            waiting = waiting[1:]
            with self.context.db.user(c, user.user_id) as conn:  # done with it, whatever comes next
                self._save(conn, user, {**state, "files": waiting})
        with self.context.db.user(c, user.user_id) as conn:
            for message_id, document in saved:
                wa_up.await_notice(conn, c, message_id, document, lang)
            if folder is not None and named:
                wa_up.add_recent(conn, c, linked.link_id, folder)
            if not waiting:
                wa_chat.clear_step(conn, c, user.user_id)
        if stopped:
            lead = "\n\n".join([*said, say("folder_gone", lang)])
            return self._list(
                user, linked, {**state, "files": waiting}, None, 0, lead=_cut(lead, 600, lines=True)
            )
        return Outgoing(self._fit(said), self._menu(lang), documents=tuple(dict.fromkeys(named)), lang=lang)

    def _said(
        self, user: CurrentUser, reply: dict[str, Any], name: str, where: str | None, lang: Language
    ) -> tuple[str, str | None, bool]:
        """What the upload's reply says on WhatsApp (PRD 7 section 5's three answers, then the
        saved one); the document it names (None: none may be named) and whether it is new."""
        if reply.get("hidden"):
            return say("hidden_copy", lang), None, False
        document = reply["document"]
        document_id = str(document["id"])
        if reply.get("duplicate"):
            with self.context.db.user(user.customer_id, user.user_id) as conn:
                named = bool(wa_chat.on_whatsapp(conn, user.customer_id, [document_id]))
            title = self._name(str(document["display_name"]))
            if reply.get("in_trash"):
                return say("already_trash", lang, title), None, False
            if not named:  # locked: never named on WhatsApp, even to a person who may open it
                return say("locked_copy", lang), None, False
            return f"{say('already', lang, title)}\n{self._link(document_id, lang)}", document_id, False
        title = self._name(name)
        line = say("saved", lang, title, where) if where else say("saved_unfiled", lang, title)
        return f"{line}\n{self._link(document_id, lang)}", document_id, True

    def _store(
        self, user: CurrentUser, item: dict[str, Any], policy: UploadPolicy, folder: str | None, batch: str
    ) -> dict[str, Any]:
        """One waiting file fetched from Meta just before and saved by the browser's upload."""
        meta = self._whatsapp()
        try:
            media = meta.media(str(item["media"]))
        except WhatsAppError as error:
            if error.code == "refused" and error.status in (400, 404, 410):
                raise _MediaGone from None
            raise
        if media.size is not None and media.size > policy.largest_limit:  # before it is fetched
            raise ApiError(413, "too_large")
        try:
            with meta.download(media.url) as stream:
                upload = UploadFile(stream, filename=str(item["name"]))
                return documents.store_upload(self.context, user, upload, policy, folder, batch)
        except WhatsAppError as error:
            if error.code == "refused" and error.status in (400, 404, 410):
                raise _MediaGone from None
            raise
        except (OSError, http.client.HTTPException):  # Meta broke off mid-file: nothing was stored
            raise WhatsAppError("unavailable") from None

    def _joined(
        self,
        user: CurrentUser,
        photos: list[dict[str, Any]],
        policy: UploadPolicy,
        folder: str | None,
        batch: str,
        name: str,
    ) -> dict[str, Any] | None:
        """The photos as one PDF named ``name``, saved by the browser's upload (ApiError as its);
        None when one can't be joined (not a photo the organisation takes, gone, too large, or not
        a plain JPEG)."""
        if sniff.JPEG not in policy.allowed_types:
            return None
        most = min(policy.limit_for(sniff.JPEG), PHOTO_BYTES)
        meta = self._whatsapp()
        with tempfile.TemporaryFile() as pdf:  # on disk: as large as the photos together
            writer = PhotosPdf(pdf)
            for photo in photos:
                try:
                    media = meta.media(str(photo["media"]))
                    if media.size is not None and media.size > most:
                        return None
                    with meta.download(media.url) as stream:
                        data = stream.read(most + 1)
                except WhatsAppError as error:
                    if error.code == "refused":
                        return None
                    raise
                except (OSError, http.client.HTTPException):
                    raise WhatsAppError("unavailable") from None
                if len(data) > most or not writer.add(data):
                    return None
            writer.close()
            pdf.seek(0)
            upload = UploadFile(pdf, filename=name)
            return documents.store_upload(self.context, user, upload, policy, folder, batch)

    # --- shared ----------------------------------------------------------------------------

    def _whatsapp(self) -> MetaWhatsApp:
        if self._meta is None:
            self._meta = MetaWhatsApp.from_settings(self.context.settings)
        return self._meta

    def _on(self, conn: Any, user: CurrentUser) -> bool:
        """The Admin's "Allow saving files from WhatsApp" (W3)."""
        settings = wa.org_settings(conn, user.customer_id)
        return settings is not None and bool(settings["uploads"])

    def _state(self, conn: Any, user: CurrentUser, *, waiting_only: bool = True) -> dict[str, Any] | None:
        """The person's upload, while it waits (``waiting_only``: with files)."""
        state = wa_chat.step(conn, user.customer_id, user.user_id)
        if state is None or state.get("step") != "upload":
            return None
        return state if state.get("files") or not waiting_only else None

    def _save(self, conn: Any, user: CurrentUser, state: dict[str, Any]) -> None:
        wa_chat.set_step(conn, user.customer_id, user.user_id, state, PICKER_MINUTES)

    def _menu(self, lang: Language) -> tuple[tuple[str, str], ...]:
        return ((f"menu.{lang}", say("menu_button", lang)),)

    def _name(self, name: str) -> str:
        return _cut(_masked(name), NAME_CHARS)  # masked before the cut, which could leave part whole

    def _link(self, document_id: str, lang: Language) -> str:
        return say("open_in_kaagaz", lang, self._url(f"/documents/{document_id}"))

    def _url(self, path: str) -> str:
        return (self.context.settings.app_url or "").rstrip("/") + path

    def _stamp(self, moment: datetime) -> str:
        local = moment.astimezone(ZoneInfo(self.context.settings.timezone))
        return f"{local.day} {local.strftime('%b')} {local.year} {local.strftime('%H.%M')}"

    def _fit(self, said: list[str]) -> str:
        """Each file's words, as many whole ones as fit WhatsApp's body; then "…"."""
        body = ""
        for line in said:
            joined = f"{body}\n\n{line}" if body else line
            if len(joined) > MAX_BODY - 2:
                return body + "\n…"
            body = joined
        return body
