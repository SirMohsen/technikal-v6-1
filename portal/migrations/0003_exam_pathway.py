from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def copy_legacy_access(apps, schema_editor):
    Exam = apps.get_model("portal", "Exam")
    Exam.objects.filter(is_free=True).update(access_type="free")
    Exam.objects.filter(is_free=False).update(access_type="premium")


def create_default_stages(apps, schema_editor):
    ExamTrack = apps.get_model("portal", "ExamTrack")
    ExamStage = apps.get_model("portal", "ExamStage")
    Exam = apps.get_model("portal", "Exam")
    ExamQuestion = apps.get_model("portal", "ExamQuestion")
    defaults = [
        (1, "آشنایی و سنجش اولیه", "سنجش اولیه سطح آمادگی؛ آزمون کوتاه و رایگان برای شروع مسیر."),
        (2, "تمرین مباحث تخصصی", "تمرین مستقل هر مبحث و سرفصل، همراه با تحلیل نقاط قوت و ضعف."),
        (3, "آزمون‌های ترکیبی", "تمرین ترکیبی از مباحث مختلف و تحلیل عملکرد بر اساس سرفصل‌ها."),
        (4, "آزمون جامع با زمان‌بندی واقعی", "شبیه‌سازی شرایط آزمون اصلی، پاسخ‌های تشریحی و ارزیابی جامع."),
        (5, "ارزیابی نهایی آمادگی", "سنجش نهایی آمادگی و دریافت پیشنهادهای مطالعاتی شخصی."),
    ]
    for track in ExamTrack.objects.all():
        stage_objects = {}
        for number, title, description in defaults:
            stage, _ = ExamStage.objects.get_or_create(
                track_id=track.pk, number=number,
                defaults={"title": title, "description": description, "order": number, "required_completion_percent": 100, "is_active": True},
            )
            stage_objects[number] = stage
        # Keep the existing published guest-friendly introductory exam as stage 1.
        intro = Exam.objects.filter(track_id=track.pk, is_published=True, guest_allowed=True).order_by("pk").first()
        if intro and not intro.stage_id:
            intro.stage_id = stage_objects[1].pk
            intro.display_order = 1
            intro.access_type = "free"
            intro.duration_minutes = 15
            available_questions = ExamQuestion.objects.filter(exam_id=intro.pk).count()
            intro.question_limit = min(5, available_questions) if available_questions else intro.question_limit
            intro.save(update_fields=["stage", "display_order", "access_type", "duration_minutes", "question_limit"])


def backfill_user_progress(apps, schema_editor):
    from django.db.models import Count, Max
    Attempt = apps.get_model("portal", "Attempt")
    Exam = apps.get_model("portal", "Exam")
    UserExamProgress = apps.get_model("portal", "UserExamProgress")
    pairs = Attempt.objects.filter(user__isnull=False).values_list("user_id", "exam_id").distinct()
    for user_id, exam_id in pairs.iterator():
        attempts = Attempt.objects.filter(user_id=user_id, exam_id=exam_id).order_by("started_at", "pk")
        completed = attempts.filter(status__in=["submitted", "expired"])
        stats = completed.aggregate(total=Count("id"), best=Max("score_percent"))
        latest = completed.order_by("-submitted_at", "-pk").first()
        first = attempts.first()
        exam = Exam.objects.get(pk=exam_id)
        best_score = stats["best"] or 0
        passed_attempt = completed.filter(score_percent__gte=exam.minimum_score).order_by("submitted_at", "pk").first()
        status = "completed" if best_score >= exam.minimum_score else "unlocked"
        UserExamProgress.objects.update_or_create(
            user_id=user_id, exam_id=exam_id,
            defaults={
                "status": status,
                "attempts_count": stats["total"] or 0,
                "best_score": best_score,
                "last_score": latest.score_percent if latest else 0,
                "unlocked_at": first.started_at if first else None,
                "completed_at": passed_attempt.submitted_at if passed_attempt else None,
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        ("portal", "0002_attemptquestion"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ExamStage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("number", models.PositiveSmallIntegerField(choices=[(1, "مرحله اول"), (2, "مرحله دوم"), (3, "مرحله سوم"), (4, "مرحله چهارم"), (5, "مرحله پنجم")])),
                ("title", models.CharField(max_length=180)),
                ("description", models.TextField(blank=True)),
                ("order", models.PositiveSmallIntegerField(default=1)),
                ("required_completion_percent", models.PositiveSmallIntegerField(default=100)),
                ("is_active", models.BooleanField(default=True)),
                ("track", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="stages", to="portal.examtrack")),
            ],
            options={"ordering": ["order", "number"], "constraints": [models.UniqueConstraint(fields=("track", "number"), name="uniq_stage_number_track")]},
        ),
        migrations.AddField(model_name="topic", name="keyword_chart", field=models.FileField(blank=True, help_text="فایل قابل دانلود چارت کلیدواژه این مبحث", null=True, upload_to="keyword_charts/")),
        migrations.AddField(model_name="exam", name="stage", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="exams", to="portal.examstage")),
        migrations.AddField(model_name="exam", name="display_order", field=models.PositiveIntegerField(default=0, help_text="ترتیب نمایش آزمون در مرحله")),
        migrations.AddField(model_name="exam", name="access_type", field=models.CharField(choices=[("free", "رایگان"), ("premium", "ویژه"), ("prerequisite", "نیازمند پیش‌نیاز")], default="premium", max_length=20)),
        migrations.AddField(model_name="exam", name="prerequisite_exam", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="unlocks_exams", to="portal.exam")),
        migrations.AddField(model_name="exam", name="minimum_score", field=models.DecimalField(decimal_places=2, default=60, help_text="حداقل امتیاز این آزمون برای باز شدن آزمون بعدی", max_digits=5)),
        migrations.AddField(model_name="exam", name="counts_for_stage_unlock", field=models.BooleanField(default=True, help_text="در محاسبه تکمیل مرحله برای باز شدن مرحله بعد لحاظ شود")),
        migrations.CreateModel(
            name="UserExamProgress",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("locked", "قفل"), ("unlocked", "باز"), ("completed", "تکمیل‌شده")], default="locked", max_length=12)),
                ("attempts_count", models.PositiveIntegerField(default=0)),
                ("best_score", models.DecimalField(decimal_places=2, default=0, max_digits=6)),
                ("last_score", models.DecimalField(decimal_places=2, default=0, max_digits=6)),
                ("unlocked_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("exam", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="user_progress", to="portal.exam")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="exam_progress", to=settings.AUTH_USER_MODEL)),
            ],
            options={"constraints": [models.UniqueConstraint(fields=("user", "exam"), name="uniq_user_exam_progress")], "indexes": [models.Index(fields=["user", "status"], name="portal_user_progress_idx")]},
        ),
        migrations.CreateModel(
            name="ExamPurchase",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("pending", "در انتظار پرداخت"), ("paid", "پرداخت‌شده"), ("refunded", "مستردشده")], default="pending", max_length=12)),
                ("amount", models.PositiveBigIntegerField(default=0, help_text="مبلغ به ریال")),
                ("provider", models.CharField(blank=True, max_length=40)),
                ("transaction_id", models.CharField(blank=True, max_length=120)),
                ("paid_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("exam", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="purchases", to="portal.exam")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="exam_purchases", to=settings.AUTH_USER_MODEL)),
            ],
            options={"constraints": [models.UniqueConstraint(fields=("user", "exam"), name="uniq_exam_purchase_user")], "indexes": [models.Index(fields=["user", "status"], name="portal_purchase_user_idx")]},
        ),
        migrations.RunPython(copy_legacy_access, migrations.RunPython.noop),
        migrations.RunPython(create_default_stages, migrations.RunPython.noop),
        migrations.RunPython(backfill_user_progress, migrations.RunPython.noop),
    ]
