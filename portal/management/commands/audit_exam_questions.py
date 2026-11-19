from django.core.management.base import BaseCommand
from django.db.models import Count, Q
from portal.models import Exam


class Command(BaseCommand):
    help = "گزارش وضعیت اتصال سؤال‌های فعال به آزمون‌ها"

    def handle(self, *args, **options):
        exams = Exam.objects.select_related("track", "topic").annotate(
            linked_total=Count("exam_questions", distinct=True),
            active_linked=Count(
                "exam_questions",
                filter=Q(
                    exam_questions__question__is_active=True,
                    exam_questions__question__topic__is_active=True,
                    exam_questions__question__topic__track__is_active=True,
                ),
                distinct=True,
            ),
        ).order_by("track__title", "title")
        if not exams.exists():
            self.stdout.write("هیچ آزمونی ثبت نشده است.")
            return
        for exam in exams:
            status = "آماده" if exam.active_linked else "بدون سؤال فعال"
            self.stdout.write(
                f"[{status}] {exam.track.title} | {exam.title} | "
                f"سؤال فعال: {exam.active_linked} | کل اتصال‌ها: {exam.linked_total} | "
                f"ظرفیت آزمون: {exam.question_limit}"
            )
