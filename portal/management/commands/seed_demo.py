from django.core.management.base import BaseCommand
from portal.models import *
class Command(BaseCommand):
    help="Create two tracks and free demo exams."
    def handle(self,*args,**kwargs):
        tracks=[
          ("execution","آزمون نظام مهندسی معماری – اجرا","آمادگی برای صلاحیت اجرا","violet"),
          ("supervision","آزمون نظام مهندسی معماری – نظارت","آمادگی برای صلاحیت نظارت","blue")]
        samples=[
          ("کدام گزینه درباره هدف اصلی مقررات ملی ساختمان صحیح‌تر است؟",["تأمین ایمنی، بهداشت و آسایش","افزایش هزینه ساخت","حذف نظارت فنی","یکسان‌سازی ظاهر بنا"],0,"مقررات ملی با هدف حفاظت از ایمنی، سلامت و آسایش بهره‌برداران تدوین شده است."),
          ("در مطالعه سؤال آزمون، نخستین اقدام مناسب چیست؟",["خواندن دقیق صورت سؤال","انتخاب گزینه بلندتر","نادیده گرفتن قیدها","پاسخ بدون بررسی"],0,"خواندن دقیق صورت سؤال و توجه به قیدها احتمال خطا را کاهش می‌دهد."),
          ("کدام روش برای مرور پاسخ‌های اشتباه مفیدتر است؟",["ثبت علت اشتباه و مرور مجدد","حذف سؤال از برنامه","حفظ گزینه بدون فهم","نادیده گرفتن توضیحات"],0,"تحلیل علت خطا پایه یادگیری پایدار است.")]
        for code,title,sub,accent in tracks:
            t,_=ExamTrack.objects.get_or_create(code=code,defaults={"title":title,"subtitle":sub,"accent":accent})
            topic,_=Topic.objects.get_or_create(track=t,slug="general",defaults={"title":"مبانی و مقررات عمومی"})
            e,created=Exam.objects.get_or_create(slug=f"{code}-free-intro",defaults={"track":t,"title":"آزمون آشنایی رایگان","description":"سه سؤال نمونه برای آشنایی با محیط آزمون.","mode":"practice","duration_minutes":10,"question_limit":3,"is_free":True,"guest_allowed":True,"is_published":True})
            if created:
                for n,(text,opts,correct,explain) in enumerate(samples,1):
                    q=Question.objects.create(topic=topic,text=text,explanation=explain,difficulty="easy")
                    for i,value in enumerate(opts): Choice.objects.create(question=q,label="ABCD"[i],text=value,is_correct=i==correct)
                    ExamQuestion.objects.create(exam=e,question=q,order=n)
        self.stdout.write(self.style.SUCCESS("Demo tracks and exams created."))
