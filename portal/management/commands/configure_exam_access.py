from django.core.management.base import BaseCommand

from portal.models import Exam, ExamStage, ExamTrack


class Command(BaseCommand):
    help = "Move the free introductory exam to stage 0 and keep the rest of the pathway login-protected."

    def add_arguments(self, parser):
        parser.add_argument("--title", default="آزمون آشنایی رایگان", help="Title of the introductory exam")

    def handle(self, *args, **options):
        title = options["title"]
        moved = 0

        for track in ExamTrack.objects.filter(is_active=True):
            stage0 = ExamStage.objects.filter(track=track, number=0, is_active=True).first()
            if not stage0:
                continue

            exams = Exam.objects.filter(
                track=track,
                title=title,
            )

            for exam in exams:
                if exam.stage_id != stage0.pk:
                    exam.stage = stage0
                    exam.access_type = "free"
                    exam.is_free = True
                    exam.guest_allowed = True
                    exam.save(update_fields=["stage", "access_type", "is_free", "guest_allowed"])
                    moved += 1

        self.stdout.write(self.style.SUCCESS(
            f"Introductory exam moved to stage 0 and configured for guest access. Updated: {moved}."
        ))
