from django.core.management.base import BaseCommand
from portal.models import ExamTrack, ExamStage

STAGES = [
    (
        0,
        "آشنایی و سنجش اولیه",
        "آزمون‌های سه‌سؤاله رایگان به سبک دولینگو. هر آزمون حدود ۷٫۵ دقیقه زمان نیاز دارد. "
        "پس از هر آزمون یک تبلیغ ۲۰ ثانیه‌ای نمایش داده می‌شود. تعداد آزمون‌ها نامحدود است. "
        "پس از دو مرحله می‌توانید به مرحله اول بروید؛ در صورت تمایل بدون ثبت‌نام و پرداخت هزینه می‌توانید به بازی ادامه دهید.",
    ),
    (
        1,
        "مطالعه و تمرین مباحث",
        "برای وارد شدن به این فصل نیاز به ورود به حساب کاربری می‌باشد.",
    ),
    (
        2,
        "آزمون بر اساس سرفصل",
        "آزمون‌های این مرحله بر اساس سرفصل‌های تعریف‌شده برگزار می‌شوند.",
    ),
    (
        3,
        "آزمون تشخیص مباحث",
        "تشخیص نقاط قوت و ضعف مباحث و تحلیل عملکرد بر اساس پاسخ‌های ثبت‌شده.",
    ),
    (
        4,
        "آزمون جامع",
        "شبیه‌سازی شرایط آزمون اصلی با زمان‌بندی واقعی و ارزیابی جامع.",
    ),
]


class Command(BaseCommand):
    help = "Create the default preparation stages (0–4) for every active exam track."

    def handle(self, *args, **options):
        created = updated = 0
        for track in ExamTrack.objects.filter(is_active=True):
            for number, title, description in STAGES:
                _, was_created = ExamStage.objects.update_or_create(
                    track=track,
                    number=number,
                    defaults={
                        "title": title,
                        "description": description,
                        "order": number,
                        "is_active": True,
                        "required_completion_percent": 0 if number <= 1 else 100,
                    },
                )
                created += int(was_created)
                updated += int(not was_created)
        self.stdout.write(
            self.style.SUCCESS(
                f"Pathway stages ready. Created: {created}; updated: {updated}."
            )
        )
        self.stdout.write(
            "Next: open Admin > Exams and assign each published exam to a stage, topic, order and access rule."
        )
