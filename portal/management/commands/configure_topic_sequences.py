from django.core.management.base import BaseCommand
from portal.models import Exam, ExamStage

class Command(BaseCommand):
    help = "Set the first three published Stage 2 exams in each topic to free, then sequence later exams as paid prerequisites."

    def handle(self, *args, **options):
        stage_ids = ExamStage.objects.filter(number=2).values_list("id", flat=True)
        topics = Exam.objects.filter(stage_id__in=stage_ids, is_published=True, topic__isnull=False).values_list("topic_id", flat=True).distinct()
        changed = 0
        for topic_id in topics:
            exams = list(Exam.objects.filter(stage_id__in=stage_ids, topic_id=topic_id, is_published=True).order_by("display_order", "id"))
            previous = None
            for index, exam in enumerate(exams):
                exam.prerequisite_exam = previous
                if index < 3:
                    exam.access_type = "free"
                    exam.is_free = True
                else:
                    exam.access_type = "premium"
                    exam.is_free = False
                # Stage 2 tests require an account so that sequence/progress is tracked.
                exam.guest_allowed = False
                exam.save(update_fields=["prerequisite_exam", "access_type", "is_free", "guest_allowed"])
                previous = exam
                changed += 1
        self.stdout.write(self.style.SUCCESS(f"Configured {changed} topic exams. First three in each topic are free; subsequent exams are paid and sequential."))
