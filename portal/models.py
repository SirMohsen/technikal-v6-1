import uuid
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.core.validators import MinValueValidator

class ExamTrack(models.Model):
    code=models.SlugField(max_length=30,unique=True)
    title=models.CharField(max_length=150)
    subtitle=models.CharField(max_length=220,blank=True)
    description=models.TextField(blank=True)
    accent=models.CharField(max_length=20,default="violet")
    is_active=models.BooleanField(default=True)
    def __str__(self): return self.title

class Topic(models.Model):
    track = models.ForeignKey(
        ExamTrack,
        on_delete=models.PROTECT,
        related_name="topics"
    )
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="children"
    )
    title = models.CharField(max_length=180)

    # پذیرش فارسی، انگلیسی، عدد، فاصله و هر متن دیگر
    slug = models.CharField(max_length=190)

    # شناسه یکتای مبحث از فایل اکسل ورودی (ای دی مبحث) — مبنای بانک سوالات
    external_id = models.CharField(
        max_length=64,
        blank=True,
        null=True,
        db_index=True,
        help_text="شناسه یکتای مبحث از فایل اکسل (ای دی مبحث). بانک سوالات بر اساس این شناسه ساخته می‌شود و به هیچ آزمونی وابسته نیست.",
    )

    # شماره فصل و شماره بند به صورت متنی طبق ساختار فایل اکسل
    chapter_number = models.CharField(max_length=50, blank=True, help_text="شماره فصل (متنی)")
    section_number = models.CharField(max_length=50, blank=True, help_text="شماره بند (متنی)")

    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    keyword_chart = models.FileField(
        upload_to="keyword_charts/",
        blank=True,
        null=True,
        help_text="قابل دانلود چارت کلیدواژه این مبحث"
    )

    class Meta:
        ordering = ["order", "title"]
        constraints = [
            models.UniqueConstraint(
                fields=["track", "slug"],
                name="uniq_topic_slug_track"
            ),
            models.UniqueConstraint(
                fields=["track", "external_id"],
                name="uniq_topic_external_id_track",
                condition=Q(external_id__isnull=False) & ~Q(external_id=""),
            ),
        ]
        indexes = [
            models.Index(fields=["track", "parent", "is_active"]),
            models.Index(fields=["external_id"]),
        ]

    def __str__(self):
        return f"{self.track.title} / {self.title}"

class Question(models.Model):
    topic = models.ForeignKey(
        Topic,
        on_delete=models.PROTECT,
        related_name="questions"
    )

    text = models.TextField()
    explanation = models.TextField(blank=True)
    source_title = models.CharField(max_length=220, blank=True)

    source_year = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1370)]
    )

    difficulty = models.CharField(
        max_length=10,
        choices=[
            ("easy", "آسان"),
            ("medium", "متوسط"),
            ("hard", "دشوار")
        ],
        default="medium"
    )

    tags = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["topic", "difficulty", "is_active"]),
            models.Index(fields=["source_year", "is_active"])
        ]

    def clean(self):
        super().clean()

        if self.source_year is not None and self.source_year < 1370:
            raise ValidationError({
                "source_year": "سال آزمون باید ۱۳۷۰ یا بالاتر باشد."
            })

    def __str__(self):
        return f"سؤال {self.pk} - {self.topic.title}"

class Choice(models.Model):
    question=models.ForeignKey(Question,on_delete=models.CASCADE,related_name="choices")
    label=models.CharField(max_length=1,choices=[(x,x) for x in "ABCD"])
    text=models.TextField()
    is_correct=models.BooleanField(default=False)
    class Meta:
        ordering=["label"]
        constraints=[models.UniqueConstraint(fields=["question","label"],name="uniq_choice_label")]
    def __str__(self): return f"{self.label}: {self.text[:50]}"

class ExamStage(models.Model):
    """A configurable stage in an exam-preparation pathway for one track."""
    STAGE_CHOICES = [
        (0, "مرحله صفرم"),
        (1, "مرحله اول"),
        (2, "مرحله دوم"),
        (3, "مرحله سوم"),
        (4, "مرحله چهارم"),
        (5, "مرحله پنجم"),
    ]
    track = models.ForeignKey(ExamTrack, on_delete=models.CASCADE, related_name="stages")
    number = models.PositiveSmallIntegerField(choices=STAGE_CHOICES)
    title = models.CharField(max_length=180)
    description = models.TextField(blank=True)
    order = models.PositiveSmallIntegerField(default=0)
    required_completion_percent = models.PositiveSmallIntegerField(default=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["order", "number"]
        constraints = [models.UniqueConstraint(fields=["track", "number"], name="uniq_stage_number_track")]

    def __str__(self):
        return f"{self.track.title} / {self.title}"


class Exam(models.Model):
    MODES=[("timed","آزمون زمان‌دار"),("practice","تمرین آموزشی"),("topic","آزمون مبحثی"),("comprehensive","آزمون جامع"),("custom","آزمون شخصی")]
    track=models.ForeignKey(ExamTrack,on_delete=models.PROTECT,related_name="exams")
    stage=models.ForeignKey(ExamStage,null=True,blank=True,on_delete=models.SET_NULL,related_name="exams")
    display_order=models.PositiveIntegerField(default=0,help_text="ترتیب نمایش آزمون در مرحله")
    ACCESS_TYPES=[("free","رایگان"),("premium","ویژه"),("prerequisite","نیازمند پیش‌نیاز")]
    access_type=models.CharField(max_length=20,choices=ACCESS_TYPES,default="premium")
    prerequisite_exam=models.ForeignKey("self",null=True,blank=True,on_delete=models.SET_NULL,related_name="unlocks_exams")
    minimum_score=models.DecimalField(max_digits=5,decimal_places=2,default=60,help_text="حداقل امتیاز امین برای باز شدن آزمون بعدی")
    counts_for_stage_unlock=models.BooleanField(default=True,help_text="در محاسبه تکمیل مرحله برای باز شدن مرحله بعد لحاظ شود")
    topic=models.ForeignKey(Topic,null=True,blank=True,on_delete=models.SET_NULL,related_name="exams")
    title=models.CharField(max_length=220); slug=models.SlugField(max_length=230,unique=True)
    description=models.TextField(blank=True); mode=models.CharField(max_length=20,choices=MODES,default="timed")
    duration_minutes=models.PositiveIntegerField(default=60); question_limit=models.PositiveIntegerField(default=5)
    is_free=models.BooleanField(default=False); guest_allowed=models.BooleanField(default=False)
    is_published=models.BooleanField(default=False); published_at=models.DateTimeField(null=True,blank=True)
    class Meta:
        indexes=[models.Index(fields=["track","is_published","mode"])]
    def clean(self):
        super().clean()
        if self.stage_id and self.track_id and self.stage.track_id != self.track_id:
            raise ValidationError({"stage": "مرحله انتخاب‌شده متعلق به مسیر آزمون دیگری است."})
        if self.prerequisite_exam_id:
            if self.prerequisite_exam_id == self.pk:
                raise ValidationError({"prerequisite_exam": "یک آزمون نمی‌تواند پیش‌نیاز خودش باشد."})
            if self.track_id and self.prerequisite_exam.track_id != self.track_id:
                raise ValidationError({"prerequisite_exam": "آزمون پیش‌نیاز باید متعلق به همین مسیر باشد."})
    def __str__(self): return self.title

class ExamQuestion(models.Model):
    exam=models.ForeignKey(Exam,on_delete=models.CASCADE,related_name="exam_questions")
    question=models.ForeignKey(Question,on_delete=models.CASCADE,related_name="exam_appearances")
    order=models.PositiveIntegerField(default=1)
    points=models.DecimalField(max_digits=5,decimal_places=2,default=1)
    class Meta:
        ordering=["order"]
        constraints=[models.UniqueConstraint(fields=["exam","question"],name="uniq_exam_question"),models.UniqueConstraint(fields=["exam","order"],name="uniq_exam_order")]

class StudyProfile(models.Model):
    user=models.OneToOneField(settings.AUTH_USER_MODEL,on_delete=models.CASCADE,related_name="study_profile")
    daily_minutes=models.PositiveSmallIntegerField(default=60)
    exam_date=models.DateField(null=True,blank=True)
    work_schedule=models.CharField(max_length=30,blank=True)
    experience_level=models.CharField(max_length=30,blank=True)
    preferred_study_time=models.CharField(max_length=20,blank=True)

class Attempt(models.Model):
    STATUS=[("active","در حال انجام"),("submitted","ثبت شده"),("expired","پایان زمان")]
    exam=models.ForeignKey(Exam,on_delete=models.PROTECT,related_name="attempts")
    user=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.SET_NULL,related_name="exam_attempts")
    guest_key=models.UUIDField(default=uuid.uuid4,editable=False,db_index=True)
    started_at=models.DateTimeField(default=timezone.now); ends_at=models.DateTimeField()
    submitted_at=models.DateTimeField(null=True,blank=True); status=models.CharField(max_length=12,choices=STATUS,default="active")
    correct_count=models.PositiveIntegerField(default=0); wrong_count=models.PositiveIntegerField(default=0)
    unanswered_count=models.PositiveIntegerField(default=0); score_percent=models.DecimalField(max_digits=6,decimal_places=2,default=0)
    class Meta: indexes=[models.Index(fields=["user","status","started_at"])]

class AttemptAnswer(models.Model):
    attempt=models.ForeignKey(Attempt,on_delete=models.CASCADE,related_name="answers")
    question=models.ForeignKey(Question,on_delete=models.CASCADE)
    selected_choice=models.ForeignKey(Choice,null=True,blank=True,on_delete=models.SET_NULL)
    marked_for_review=models.BooleanField(default=False)
    updated_at=models.DateTimeField(auto_now=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=["attempt","question"],name="uniq_attempt_answer")]


class AttemptQuestion(models.Model):
    """The immutable question set assigned to one exam attempt."""
    attempt=models.ForeignKey(Attempt,on_delete=models.CASCADE,related_name="attempt_questions")
    question=models.ForeignKey(Question,on_delete=models.CASCADE,related_name="attempt_appearances")
    order=models.PositiveIntegerField(default=1)
    points=models.DecimalField(max_digits=5,decimal_places=2,default=1)
    class Meta:
        ordering=["order","pk"]
        constraints=[
            models.UniqueConstraint(fields=["attempt","question"],name="uniq_attempt_question"),
            models.UniqueConstraint(fields=["attempt","order"],name="uniq_attempt_order"),
        ]


class UserExamProgress(models.Model):
    """Cached per-user progress; Attempt/AttemptAnswer remain the source of truth."""
    STATUS_CHOICES = [("locked","قفل"),("unlocked","باز"),("completed","تکمیل‌شده")]
    user = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE,related_name="exam_progress")
    exam = models.ForeignKey(Exam,on_delete=models.CASCADE,related_name="user_progress")
    status = models.CharField(max_length=12,choices=STATUS_CHOICES,default="locked")
    attempts_count = models.PositiveIntegerField(default=0)
    best_score = models.DecimalField(max_digits=6,decimal_places=2,default=0)
    last_score = models.DecimalField(max_digits=6,decimal_places=2,default=0)
    unlocked_at = models.DateTimeField(null=True,blank=True)
    completed_at = models.DateTimeField(null=True,blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints=[models.UniqueConstraint(fields=["user","exam"],name="uniq_user_exam_progress")]
        indexes=[models.Index(fields=["user","status"],name="portal_user_progress_idx")]

    def __str__(self):
        return f"{self.user} / {self.exam} / {self.get_status_display()}"


class ExamPurchase(models.Model):
    STATUS_CHOICES = [("pending","در انتظار پرداخت"),("paid","پرداخت‌شده"),("refunded","مستردشده")]
    user = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE,related_name="exam_purchases")
    exam = models.ForeignKey(Exam,on_delete=models.PROTECT,related_name="purchases")
    status = models.CharField(max_length=12,choices=STATUS_CHOICES,default="pending")
    amount = models.PositiveBigIntegerField(default=0,help_text="مبلغ به ریال")
    provider = models.CharField(max_length=40,blank=True)
    transaction_id = models.CharField(max_length=120,blank=True)
    paid_at = models.DateTimeField(null=True,blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints=[models.UniqueConstraint(fields=["user","exam"],name="uniq_exam_purchase_user")]
        indexes=[models.Index(fields=["user","status"],name="portal_purchase_user_idx")]

    def __str__(self):
        return f"{self.user} / {self.exam} / {self.get_status_display()}"
