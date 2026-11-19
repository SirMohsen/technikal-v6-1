from django.contrib import admin, messages
from django.forms.models import BaseInlineFormSet
from django.db.models import Q, TextField
from django.db.models.functions import Cast
from django.core.exceptions import ValidationError
from django.urls import path, reverse
from django.utils.html import format_html

from .models import *
from .question_import import get_assign_view, get_import_view, sample_file


@admin.register(ExamTrack)
class TrackAdmin(admin.ModelAdmin):
    list_display = ("title", "code", "is_active")


@admin.register(ExamStage)
class ExamStageAdmin(admin.ModelAdmin):
    list_display = ("title", "track", "number", "order", "required_completion_percent", "is_active")
    list_filter = ("track", "number", "is_active")
    list_editable = ("order", "required_completion_percent", "is_active")
    ordering = ("track", "order")


@admin.register(Topic)
class TopicAdmin(admin.ModelAdmin):
    list_display = ("title", "external_id", "track", "chapter_number", "section_number", "parent", "order", "is_active", "has_keyword_chart")
    list_filter = ("track", "is_active")
    search_fields = ("title", "slug", "external_id", "chapter_number", "section_number")
    list_editable = ("order", "is_active")

    @admin.display(boolean=True, description="چارت کلیدواژه")
    def has_keyword_chart(self, obj):
        return bool(obj.keyword_chart)


class ChoiceInlineFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        active = [form for form in self.forms if form.cleaned_data and not form.cleaned_data.get("DELETE", False)]
        if not active:
            return
        if len(active) != 4:
            raise ValidationError("هر سؤال باید دقیقاً چهار گزینه داشته باشد.")
        if sum(bool(form.cleaned_data.get("is_correct")) for form in active) != 1:
            raise ValidationError("هر سؤال باید دقیقاً یک گزینه صحیح داشته باشد.")


class ChoiceInline(admin.TabularInline):
    model = Choice
    formset = ChoiceInlineFormSet
    extra = 4
    min_num = 4
    max_num = 4
    validate_min = True
    validate_max = True


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    change_list_template = "admin/portal/question/change_list.html"
    list_display = ("id", "topic", "topic_external_id", "source_title", "source_year", "difficulty", "is_active", "exam_count", "attempt_count")
    list_filter = ("topic__track", "topic", "difficulty", "source_year", "is_active")
    search_fields = ("text", "explanation", "source_title", "topic__slug", "topic__external_id", "topic__title")
    inlines = [ChoiceInline]
    actions = ("archive_questions", "restore_questions", "force_delete_questions")

    def get_search_results(self, request, queryset, search_term):
        """Search question content, topic external_id / slug, and serialized tags."""
        terms = search_term.split()
        if terms:
            queryset = queryset.alias(_tags_search_text=Cast("tags", TextField()))
            for term in terms:
                queryset = queryset.filter(
                    Q(text__icontains=term)
                    | Q(explanation__icontains=term)
                    | Q(source_title__icontains=term)
                    | Q(topic__slug__icontains=term)
                    | Q(topic__external_id__icontains=term)
                    | Q(topic__title__icontains=term)
                    | Q(_tags_search_text__icontains=term)
                )
        return queryset, False

    @admin.display(description="ای‌دی مبحث")
    def topic_external_id(self, obj):
        return obj.topic.external_id or "—"

    @admin.display(description="آزمون‌های متصل")
    def exam_count(self, obj):
        return obj.exam_appearances.count()

    @admin.display(description="پاسخ‌های ثبت‌شده")
    def attempt_count(self, obj):
        return AttemptAnswer.objects.filter(question=obj).count()

    @admin.action(description="غیرفعال‌سازی سؤال‌های انتخاب‌شده (حذف امن از آزمون‌های آینده)")
    def archive_questions(self, request, queryset):
        count = queryset.update(is_active=False)
        self.message_user(request, f"{count} سؤال غیرفعال شد. سوابق آزمون‌های قبلی حفظ می‌شوند.", messages.SUCCESS)

    @admin.action(description="فعال‌سازی سؤال‌های انتخاب‌شده")
    def restore_questions(self, request, queryset):
        count = queryset.update(is_active=True)
        self.message_user(request, f"{count} سؤال فعال شد.", messages.SUCCESS)

    @admin.action(description="حذف دائمی سؤال‌های انتخاب‌شده (حتی در صورت اتصال به آزمون)")
    def force_delete_questions(self, request, queryset):
        """Admin can delete any question as requested. Linked exam relations cascade."""
        count = queryset.count()
        queryset.delete()
        self.message_user(request, f"{count} سؤال به‌صورت دائمی حذف شد.", messages.SUCCESS)

    def has_delete_permission(self, request, obj=None):
        # Admin can edit and delete all questions (requirement #3).
        return True

    def get_urls(self):
        custom = [
            path("bulk-import/", self.admin_site.admin_view(get_import_view(self)), name="portal_question_bulk_import"),
            path("bulk-import/template/", self.admin_site.admin_view(sample_file), name="portal_question_import_template"),
            path("assign-to-exam/", self.admin_site.admin_view(get_assign_view(self)), name="portal_question_assign_to_exam"),
        ]
        return custom + super().get_urls()


class ExamQuestionInline(admin.TabularInline):
    model = ExamQuestion
    extra = 1
    autocomplete_fields = ["question"]


@admin.register(Exam)
class ExamAdmin(admin.ModelAdmin):
    list_display = ("title", "track", "stage", "display_order", "mode", "active_question_count", "question_limit", "access_type", "is_published")
    list_filter = ("track", "stage", "mode", "access_type", "is_published")
    search_fields = ("title", "description")
    prepopulated_fields = {"slug": ("title",)}
    autocomplete_fields = ("prerequisite_exam",)
    inlines = [ExamQuestionInline]
    actions = ("attach_active_topic_questions",)

    @admin.display(description="سؤال فعال / متصل")
    def active_question_count(self, obj):
        return obj.exam_questions.filter(
            question__is_active=True, question__topic__is_active=True,
            question__topic__track__is_active=True
        ).count()

    @admin.action(description="افزودن تمام سؤالات فعال مبحث اصلی هر آزمون به همان آزمون")
    def attach_active_topic_questions(self, request, queryset):
        added = skipped = 0
        for exam in queryset.select_related("track", "topic"):
            if not exam.topic_id or exam.topic.track_id != exam.track_id:
                skipped += 1
                continue
            existing = set(exam.exam_questions.values_list("question_id", flat=True))
            questions = list(Question.objects.filter(
                topic=exam.topic, is_active=True, topic__is_active=True,
                topic__track__is_active=True
            ).exclude(pk__in=existing).order_by("pk"))
            next_order = exam.exam_questions.order_by("-order").values_list("order", flat=True).first() or 0
            ExamQuestion.objects.bulk_create([
                ExamQuestion(exam=exam, question=q, order=next_order+i+1)
                for i, q in enumerate(questions)
            ], batch_size=1000)
            added += len(questions)
        self.message_user(request, f"{added} سؤال فعال به آزمون‌ها اضافه شد؛ {skipped} آزمون به دلیل نداشتن مبحث اصلی یا عدم تطابق رشته بررسی نشد.", messages.SUCCESS if added else messages.WARNING)


class AnswerInline(admin.TabularInline):
    model = AttemptAnswer
    extra = 0
    readonly_fields = ("question", "selected_choice", "marked_for_review", "updated_at")


@admin.register(Attempt)
class AttemptAdmin(admin.ModelAdmin):
    list_display = ("id", "exam", "user", "status", "started_at", "score_percent")
    list_filter = ("status", "exam__track")
    inlines = [AnswerInline]


@admin.register(UserExamProgress)
class UserExamProgressAdmin(admin.ModelAdmin):
    list_display = ("user", "exam", "status", "attempts_count", "best_score", "last_score", "updated_at")
    list_filter = ("status", "exam__track", "exam__stage")
    search_fields = ("user__username", "exam__title")
    readonly_fields = ("attempts_count", "best_score", "last_score", "unlocked_at", "completed_at", "updated_at")


@admin.register(ExamPurchase)
class ExamPurchaseAdmin(admin.ModelAdmin):
    list_display = ("user", "exam", "status", "amount", "provider", "transaction_id", "paid_at")
    list_filter = ("status", "exam__track")
    search_fields = ("user__username", "exam__title", "transaction_id")
    actions = ("mark_as_paid",)

    @admin.action(description="ثبت پرداخت موفق برای خریدهای انتخاب‌شده")
    def mark_as_paid(self, request, queryset):
        from django.utils import timezone
        count = queryset.exclude(status="paid").update(status="paid", paid_at=timezone.now())
        self.message_user(request, f"دسترسی {count} خرید فعال شد. اتصال درگاه پرداخت باید جداگانه انجام شود.", messages.SUCCESS)


admin.site.register(StudyProfile)
