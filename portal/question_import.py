"""Validated bulk import of exam questions from XLSX or CSV files.

Primary Excel structure (Persian headers — basis of the question bank):
  ای دی مبحث | نام مبحث | شماره فصل | شماره بند | سوال | جواب1 | جواب2 | جواب3 | جواب4 | جواب صحیح | پاسخ تشریحی

- ای دی مبحث: unique identifier (stored as Topic.external_id)
- شماره فصل / شماره بند: text fields
- The question bank is independent of any exam; admin assigns questions to exams
  later via the assign view (by unique topic external_id / topic).

Legacy English headers are still accepted for backward compatibility.
"""

import csv
import io
import re
import json

from django.contrib import messages
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods
from openpyxl import Workbook, load_workbook
from django.db.models import Q
from .models import Choice, Exam, ExamQuestion, ExamTrack, Question, Topic


# Primary (new) Persian headers
PERSIAN_HEADERS = [
    "ای دی مبحث",
    "نام مبحث",
    "شماره فصل",
    "شماره بند",
    "سوال",
    "جواب1",
    "جواب2",
    "جواب3",
    "جواب4",
    "جواب صحیح",
    "پاسخ تشریحی",
]

# Aliases / normalized keys for Persian format
PERSIAN_ALIASES = {
    "ای دی مبحث": "topic_id",
    "ایدی مبحث": "topic_id",
    "id مبحث": "topic_id",
    "topic_id": "topic_id",
    "نام مبحث": "topic_name",
    "نام_مبحث": "topic_name",
    "شماره فصل": "chapter",
    "شماره_فصل": "chapter",
    "شماره بند": "section",
    "شماره_بند": "section",
    "سوال": "question",
    "سؤال": "question",
    "جواب1": "option_a",
    "جواب ۲": "option_b",
    "جواب2": "option_b",
    "جواب3": "option_c",
    "جواب4": "option_d",
    "جواب صحیح": "correct",
    "جواب_صحیح": "correct",
    "پاسخ تشریحی": "explanation",
    "پاسخ_تشریحی": "explanation",
}

# Legacy English headers
LEGACY_HEADERS = [
    "track_code", "topic_slug", "question", "option_a", "option_b",
    "option_c", "option_d", "correct_option", "explanation",
    "source_title", "source_year", "difficulty", "tags",
]

MAX_UPLOAD_SIZE = 10 * 1024 * 1024
MAX_ROWS = 10000


def sample_file(request):
    """Download a sample Excel file with the new Persian column structure."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Questions"
    ws.append(PERSIAN_HEADERS)
    ws.append([
        "1001",
        "مبحث نمونه",
        "۱",
        "۱-۱",
        "متن نمونه سؤال را اینجا وارد کنید",
        "متن گزینه اول",
        "متن گزینه دوم",
        "متن گزینه سوم",
        "متن گزینه چهارم",
        "1",  # جواب صحیح: 1 / 2 / 3 / 4 یا A/B/C/D
        "توضیح تشریحی پاسخ را اینجا وارد کنید",
    ])

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = "A1:K2"

    for col, width in {
        "A": 14, "B": 22, "C": 12, "D": 12, "E": 48,
        "F": 28, "G": 28, "H": 28, "I": 28, "J": 14, "K": 40,
    }.items():
        ws.column_dimensions[col].width = width

    output = io.BytesIO()
    wb.save(output)

    response = HttpResponse(
        output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = (
        'attachment; filename="question_bank_template.xlsx"'
    )
    return response


def _normalize_header(h):
    text = str(h or "").strip().lower()
    # remove zero-width and extra spaces
    text = re.sub(r"[\u200c\u200f\u202a-\u202e]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


def _map_headers(raw_headers):
    """Return (format, index_map) where format is 'persian' or 'legacy'."""
    normalized = [_normalize_header(h) for h in raw_headers]

    # Try Persian first
    persian_map = {}
    for i, h in enumerate(normalized):
        key = PERSIAN_ALIASES.get(h) or PERSIAN_ALIASES.get(h.replace(" ", ""))
        if key:
            persian_map[key] = i

    required_persian = {"topic_id", "topic_name", "question", "option_a", "option_b", "option_c", "option_d", "correct"}
    if required_persian.issubset(persian_map.keys()):
        return "persian", persian_map

    # Legacy English
    legacy_norm = [h.replace(" ", "_") for h in normalized]
    legacy_map = {}
    for i, h in enumerate(legacy_norm):
        if h in LEGACY_HEADERS:
            legacy_map[h] = i
    if all(k in legacy_map for k in ("track_code", "topic_slug", "question", "option_a", "option_b", "option_c", "option_d", "correct_option")):
        return "legacy", legacy_map

    raise ValueError(
        "ستون‌های فایل با ساختار مورد انتظار مطابقت ندارد. "
        "ساختار اصلی: ای دی مبحث | نام مبحث | شماره فصل | شماره بند | سوال | جواب1 | جواب2 | جواب3 | جواب4 | جواب صحیح | پاسخ تشریحی"
    )


def _read_rows(upload):
    name = upload.name.lower()

    if name.endswith(".xlsx"):
        wb = load_workbook(upload, read_only=True, data_only=True)
        ws = wb.active
        values = ws.iter_rows(values_only=True)
        headers = next(values, None)

        if not headers:
            raise ValueError("فایل اکسل خالی است یا ردیف عنوان ستون‌ها را ندارد.")

        fmt, indexes = _map_headers(headers)
        rows = []

        for number, values_row in enumerate(values, start=2):
            if not values_row or not any(v not in (None, "") for v in values_row):
                continue

            row_dict = {
                k: (values_row[i] if i < len(values_row) else None)
                for k, i in indexes.items()
            }
            rows.append((number, row_dict, fmt))

            if len(rows) > MAX_ROWS:
                raise ValueError(f"حداکثر تعداد مجاز در هر فایل {MAX_ROWS} سؤال است.")

        wb.close()
        return rows

    if name.endswith(".csv"):
        raw = upload.read().decode("utf-8-sig")
        reader = csv.reader(io.StringIO(raw))
        headers = next(reader, None)
        if not headers:
            raise ValueError("فایل CSV عنوان ستون‌ها را ندارد.")

        fmt, indexes = _map_headers(headers)
        rows = []
        for number, values_row in enumerate(reader, start=2):
            if not any(v not in (None, "") for v in values_row):
                continue
            row_dict = {
                k: (values_row[i] if i < len(values_row) else None)
                for k, i in indexes.items()
            }
            rows.append((number, row_dict, fmt))
            if len(rows) > MAX_ROWS:
                raise ValueError(f"حداکثر تعداد مجاز در هر فایل {MAX_ROWS} سؤال است.")
        return rows

    raise ValueError("فقط فایل Excel با پسوند xlsx یا فایل CSV پشتیبانی می‌شود.")


def _clean(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def _normalize_correct(value):
    """Accept 1/2/3/4 or A/B/C/D (Persian/Arabic digits too)."""
    text = _clean(value).upper()
    text = text.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
    mapping = {
        "1": "A", "A": "A", "الف": "A",
        "2": "B", "B": "B", "ب": "B",
        "3": "C", "C": "C", "ج": "C",
        "4": "D", "D": "D", "د": "D",
    }
    return mapping.get(text, text)


def _validate_year(value):
    if value is None or str(value).strip() == "":
        return None
    text = _clean(value)
    translation = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    text = text.translate(translation)
    if re.fullmatch(r"\d+\.0+", text):
        text = text.split(".")[0]
    if not re.fullmatch(r"\d+", text):
        raise ValueError
    year = int(text)
    if year <= 1380:
        raise ValueError
    return year


def _parse_tags(value):
    if value is None or value == "":
        return []
    if isinstance(value, bool):
        return [value]
    if isinstance(value, (int, float)):
        return [value]
    text = _clean(value)
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            if not isinstance(parsed, list):
                raise ValueError("مقدار tags در قالب JSON باید آرایه باشد.")
            return parsed
        except json.JSONDecodeError as exc:
            raise ValueError("ساختار JSON ستون tags معتبر نیست.") from exc
    parts = [part.strip() for part in re.split(r"[,;|\n]", text) if part.strip()]
    result = []
    for part in parts:
        normalized = part.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
        if re.fullmatch(r"-?\d+", normalized):
            result.append(int(normalized))
        elif re.fullmatch(r"-?\d+\.\d+", normalized):
            result.append(float(normalized))
        else:
            result.append(part)
    return result


def _validate(rows, default_track_code=None):
    """Validate rows. default_track_code used for Persian format when track is not in file."""
    errors = []
    prepared = []
    topic_cache = {}
    existing_text_cache = {}
    seen = set()

    # Default track for Persian imports (admin can choose; otherwise first active)
    default_track = None
    if default_track_code:
        default_track = ExamTrack.objects.filter(code__iexact=default_track_code).first()
    if default_track is None:
        default_track = ExamTrack.objects.filter(is_active=True).first()

    for row_number, row, fmt in rows:
        problems = []

        if fmt == "persian":
            topic_id = _clean(row.get("topic_id"))
            topic_name = _clean(row.get("topic_name"))
            chapter = _clean(row.get("chapter"))
            section = _clean(row.get("section"))
            question_text = _clean(row.get("question"))
            options = {
                "A": _clean(row.get("option_a")),
                "B": _clean(row.get("option_b")),
                "C": _clean(row.get("option_c")),
                "D": _clean(row.get("option_d")),
            }
            correct = _normalize_correct(row.get("correct"))
            explanation = _clean(row.get("explanation"))
            difficulty = "medium"
            year = None
            tags = []
            source_title = ""
            track = default_track
            topic_slug = f"ext-{topic_id}" if topic_id else ""

            if not topic_id:
                problems.append("ای دی مبحث الزامی است و باید یکتا باشد")
            if not topic_name:
                problems.append("نام مبحث الزامی است")
            if not question_text:
                problems.append("متن سؤال خالی است")
            empty_options = [k for k, v in options.items() if not v]
            if empty_options:
                problems.append("متن گزینه‌های " + ", ".join(empty_options) + " خالی است")
            if correct not in {"A", "B", "C", "D"}:
                problems.append("جواب صحیح باید یکی از ۱، ۲، ۳، ۴ یا A، B، C، D باشد")
            if not track:
                problems.append("هیچ مسیر آزمونی (ExamTrack) فعالی در سامانه تعریف نشده است")

            cache_key = (track.id if track else None, topic_id)
            if cache_key not in topic_cache and track and topic_id:
                topic = Topic.objects.filter(track=track, external_id=topic_id).first()
                if not topic:
                    topic = Topic.objects.filter(track=track, slug=topic_slug).first()
                topic_cache[cache_key] = topic
            else:
                topic = topic_cache.get(cache_key)

            duplicate_key = (
                track.id if track else None,
                topic_id,
                re.sub(r"\s+", " ", question_text).casefold(),
            ) if track and topic_id and question_text else None

            if duplicate_key and duplicate_key in seen:
                problems.append("این سؤال در همین فایل تکراری است")
            elif duplicate_key:
                seen.add(duplicate_key)
                if topic:
                    if topic.pk not in existing_text_cache:
                        existing_text_cache[topic.pk] = {
                            re.sub(r"\s+", " ", value).casefold()
                            for value in Question.objects.filter(topic=topic).values_list("text", flat=True)
                        }
                    if duplicate_key[2] in existing_text_cache[topic.pk]:
                        problems.append("این سؤال قبلاً در بانک سؤالات ثبت شده است")

            if problems:
                errors.append(f"ردیف {row_number}: " + "؛ ".join(problems))
            else:
                prepared.append({
                    "fmt": "persian",
                    "topic": topic,
                    "track": track,
                    "topic_id": topic_id,
                    "topic_name": topic_name,
                    "topic_slug": topic_slug,
                    "chapter": chapter,
                    "section": section,
                    "text": question_text,
                    "explanation": explanation,
                    "source_title": source_title[:220],
                    "source_year": year,
                    "difficulty": difficulty,
                    "tags": tags,
                    "options": options,
                    "correct": correct,
                })

        else:  # legacy
            track_code = _clean(row.get("track_code")).lower()
            topic_slug = _clean(row.get("topic_slug"))
            question_text = _clean(row.get("question"))
            options = {
                letter: _clean(row.get(f"option_{letter.lower()}"))
                for letter in "ABCD"
            }
            correct = _normalize_correct(row.get("correct_option"))
            difficulty = _clean(row.get("difficulty") or "medium").lower()
            if difficulty in {"آسان", "ساده"}:
                difficulty = "easy"
            if difficulty == "متوسط":
                difficulty = "medium"
            if difficulty in {"سخت", "دشوار"}:
                difficulty = "hard"

            key = (track_code, topic_slug)
            if key not in topic_cache:
                track = ExamTrack.objects.filter(code__iexact=track_code).first()
                topic = None
                if track and topic_slug:
                    topic = Topic.objects.filter(track=track, slug=topic_slug).first()
                topic_cache[key] = (track, topic)
            track, topic = topic_cache[key]

            if not track_code or not topic_slug:
                problems.append("کد رشته و شناسه منبع (topic_slug) الزامی است")
            if track_code and track_code not in {"execution", "supervision"} and not track:
                problems.append(f"رشته آزمون {track_code} در پایگاه داده تعریف نشده است")
            if track_code and not track:
                problems.append(f"رشته آزمون {track_code} در پایگاه داده تعریف نشده است")
            if not question_text:
                problems.append("متن سؤال خالی است")
            empty_options = [x for x, value in options.items() if not value]
            if empty_options:
                problems.append("متن گزینه‌های " + ", ".join(empty_options) + " خالی است")
            if correct not in {"A", "B", "C", "D"}:
                problems.append("correct_option باید یکی از A، B، C یا D باشد")
            if difficulty not in {"easy", "medium", "hard"}:
                problems.append("difficulty باید easy، medium یا hard باشد")

            year = None
            try:
                year = _validate_year(row.get("source_year"))
            except (TypeError, ValueError, OverflowError):
                problems.append("سال منبع باید یک عدد معتبر بعد از ۱۳۸۰ باشد")

            duplicate_key = (
                track_code,
                topic_slug,
                re.sub(r"\s+", " ", question_text).casefold(),
            ) if track and topic_slug and question_text else None
            if duplicate_key and duplicate_key in seen:
                problems.append("این سؤال در همین فایل تکراری است")
            elif duplicate_key:
                seen.add(duplicate_key)
                if topic:
                    if topic.pk not in existing_text_cache:
                        existing_text_cache[topic.pk] = {
                            re.sub(r"\s+", " ", value).casefold()
                            for value in Question.objects.filter(topic=topic).values_list("text", flat=True)
                        }
                    if duplicate_key[2] in existing_text_cache[topic.pk]:
                        problems.append("این سؤال قبلاً در بانک سؤالات ثبت شده است")

            parsed_tags = []
            try:
                parsed_tags = _parse_tags(row.get("tags"))
            except (TypeError, ValueError) as exc:
                problems.append(f"مقدار tags معتبر نیست: {exc}")

            if problems:
                errors.append(f"ردیف {row_number}: " + "؛ ".join(problems))
            else:
                prepared.append({
                    "fmt": "legacy",
                    "topic": topic,
                    "track": track,
                    "topic_slug": topic_slug,
                    "topic_id": None,
                    "topic_name": None,
                    "chapter": "",
                    "section": "",
                    "text": question_text,
                    "explanation": _clean(row.get("explanation")),
                    "source_title": _clean(row.get("source_title"))[:220],
                    "source_year": year,
                    "difficulty": difficulty,
                    "tags": parsed_tags,
                    "options": options,
                    "correct": correct,
                })

    return prepared, errors


def get_import_view(model_admin):

    @require_http_methods(["GET", "POST"])
    def view(request):

        if not model_admin.has_add_permission(request):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied

        tracks = ExamTrack.objects.filter(is_active=True).order_by("title")

        if request.method == "POST":
            upload = request.FILES.get("file")
            track_code = _clean(request.POST.get("track_code"))

            if not upload:
                messages.error(request, "ابتدا فایل Excel یا CSV را انتخاب کن.")
            elif upload.size > MAX_UPLOAD_SIZE:
                messages.error(request, "حجم فایل نباید بیشتر از ۱۰ مگابایت باشد.")
            else:
                try:
                    rows = _read_rows(upload)
                    if not rows:
                        raise ValueError("هیچ ردیف سؤالی در فایل پیدا نشد.")

                    prepared, errors = _validate(rows, default_track_code=track_code or None)

                    selected_exam = None
                    exam_id = _clean(request.POST.get("exam_id"))
                    if exam_id:
                        selected_exam = Exam.objects.select_related("track").filter(pk=exam_id).first()
                        if selected_exam is None:
                            errors.append("آزمون انتخاب‌شده معتبر نیست.")
                        else:
                            mismatched = [
                                item for item in prepared
                                if item["track"] and item["track"].id != selected_exam.track_id
                            ]
                            if mismatched:
                                errors.append(
                                    "رشته آزمون انتخاب‌شده با رشته یک یا چند سؤال مطابقت ندارد."
                                )

                    if errors:
                        return render(
                            request,
                            "admin/portal/question/import.html",
                            {
                                **model_admin.admin_site.each_context(request),
                                "title": "ورود انبوه سؤالات (بانک سؤالات)",
                                "opts": model_admin.model._meta,
                                "errors": errors[:100],
                                "error_count": len(errors),
                                "valid_count": len(prepared),
                                "exams": Exam.objects.select_related("track").order_by("title"),
                                "tracks": tracks,
                                "selected_track": track_code,
                            },
                        )

                    created_questions = 0
                    created_topics = 0
                    linked = 0

                    with transaction.atomic():
                        for item in prepared:
                            track = item["track"]
                            topic = item["topic"]

                            if item["fmt"] == "persian":
                                if topic is None:
                                    topic, was_created = Topic.objects.get_or_create(
                                        track=track,
                                        external_id=item["topic_id"],
                                        defaults={
                                            "title": item["topic_name"],
                                            "slug": item["topic_slug"],
                                            "chapter_number": item["chapter"],
                                            "section_number": item["section"],
                                            "is_active": True,
                                        },
                                    )
                                    if was_created:
                                        created_topics += 1
                                    else:
                                        # update name / chapter / section if changed
                                        updated = False
                                        if item["topic_name"] and topic.title != item["topic_name"]:
                                            topic.title = item["topic_name"]
                                            updated = True
                                        if item["chapter"] and topic.chapter_number != item["chapter"]:
                                            topic.chapter_number = item["chapter"]
                                            updated = True
                                        if item["section"] and topic.section_number != item["section"]:
                                            topic.section_number = item["section"]
                                            updated = True
                                        if updated:
                                            topic.save()
                                else:
                                    # ensure external_id and metadata
                                    changed = False
                                    if not topic.external_id:
                                        topic.external_id = item["topic_id"]
                                        changed = True
                                    if item["topic_name"] and topic.title != item["topic_name"]:
                                        topic.title = item["topic_name"]
                                        changed = True
                                    if item["chapter"]:
                                        topic.chapter_number = item["chapter"]
                                        changed = True
                                    if item["section"]:
                                        topic.section_number = item["section"]
                                        changed = True
                                    if changed:
                                        topic.save()
                            else:
                                # legacy: create topic by slug if missing
                                if topic is None:
                                    topic, was_created = Topic.objects.get_or_create(
                                        track=track,
                                        slug=item["topic_slug"],
                                        defaults={
                                            "title": item["topic_slug"],
                                            "is_active": True,
                                        },
                                    )
                                    if was_created:
                                        created_topics += 1

                            question = Question.objects.create(
                                topic=topic,
                                text=item["text"],
                                explanation=item["explanation"],
                                source_title=item["source_title"],
                                source_year=item["source_year"],
                                difficulty=item["difficulty"],
                                tags=item["tags"],
                                is_active=True,
                            )
                            created_questions += 1

                            for letter, text in item["options"].items():
                                Choice.objects.create(
                                    question=question,
                                    label=letter,
                                    text=text,
                                    is_correct=(letter == item["correct"]),
                                )

                            if selected_exam:
                                next_order = (
                                    ExamQuestion.objects.filter(exam=selected_exam)
                                    .order_by("-order")
                                    .values_list("order", flat=True)
                                    .first()
                                    or 0
                                ) + 1
                                ExamQuestion.objects.create(
                                    exam=selected_exam,
                                    question=question,
                                    order=next_order,
                                )
                                linked += 1

                    msg = f"{created_questions} سؤال به بانک سؤالات اضافه شد."
                    if created_topics:
                        msg += f" {created_topics} مبحث جدید ایجاد شد."
                    if linked:
                        msg += f" {linked} سؤال به آزمون انتخاب‌شده متصل شد."
                    messages.success(request, msg)
                    return redirect("admin:portal_question_changelist")

                except ValueError as exc:
                    messages.error(request, str(exc))
                except Exception as exc:
                    messages.error(request, f"خطا در پردازش فایل: {exc}")

        return render(
            request,
            "admin/portal/question/import.html",
            {
                **model_admin.admin_site.each_context(request),
                "title": "ورود انبوه سؤالات (بانک سؤالات)",
                "opts": model_admin.model._meta,
                "exams": Exam.objects.select_related("track").order_by("title"),
                "tracks": tracks,
            },
        )

    return view


def get_assign_view(model_admin):
    """Assign questions from the bank (by topic / external_id) to an exam."""

    @require_http_methods(["GET", "POST"])
    def view(request):
        if not model_admin.has_change_permission(request):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied

        exams = Exam.objects.select_related("track", "stage").order_by("track__title", "title")
        topics = Topic.objects.filter(is_active=True).select_related("track").order_by("track__title", "order", "title")

        if request.method == "POST":
            exam_id = request.POST.get("exam_id")
            topic_id = request.POST.get("topic_id")
            exam = Exam.objects.filter(pk=exam_id).select_related("track").first()
            topic = Topic.objects.filter(pk=topic_id).select_related("track").first()

            if not exam or not topic:
                messages.error(request, "آزمون یا مبحث انتخاب‌شده معتبر نیست.")
            elif topic.track_id != exam.track_id:
                messages.error(request, "مبحث و آزمون باید متعلق به یک مسیر باشند.")
            else:
                existing = set(exam.exam_questions.values_list("question_id", flat=True))
                questions = list(
                    Question.objects.filter(
                        topic=topic, is_active=True, topic__is_active=True
                    ).exclude(pk__in=existing).order_by("pk")
                )
                start_order = (
                    ExamQuestion.objects.filter(exam=exam)
                    .order_by("-order")
                    .values_list("order", flat=True)
                    .first()
                    or 0
                ) + 1

                with transaction.atomic():
                    ExamQuestion.objects.bulk_create(
                        [
                            ExamQuestion(exam=exam, question=q, order=start_order + index)
                            for index, q in enumerate(questions)
                        ],
                        batch_size=1000,
                    )

                if questions:
                    messages.success(
                        request,
                        f"{len(questions)} سؤال از مبحث «{topic.title}» "
                        f"(ای‌دی: {topic.external_id or topic.slug}) به آزمون «{exam.title}» اضافه شد.",
                    )
                else:
                    messages.info(
                        request,
                        "سؤال جدیدی برای افزودن وجود نداشت؛ "
                        "یا همه سؤالات این مبحث قبلاً به آزمون متصل شده‌اند.",
                    )
                return redirect("admin:portal_question_changelist")

        return render(
            request,
            "admin/portal/question/assign.html",
            {
                **model_admin.admin_site.each_context(request),
                "title": "اختصاص گروهی سؤالات به آزمون (بر اساس ای‌دی مبحث)",
                "opts": model_admin.model._meta,
                "exams": exams,
                "topics": topics,
                "selected_exam_id": request.POST.get("exam_id", ""),
                "selected_topic_id": request.POST.get("topic_id", ""),
            },
        )

    return view
