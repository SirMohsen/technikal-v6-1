import json
from collections import defaultdict
from decimal import Decimal
from django.contrib import messages
from django.contrib.auth import login,logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm,AuthenticationForm
from django.db import transaction
from django.http import JsonResponse,HttpResponseBadRequest,HttpResponseForbidden
from django.shortcuts import get_object_or_404,redirect,render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from .models import *

def home(request):
    return render(request,"portal/home.html",{"tracks":ExamTrack.objects.filter(is_active=True)})

def stage_is_unlocked(user, stage):
    if not stage.is_active:
        return False
    # Stage 0 (آشنایی و سنجش اولیه) is always open — Duolingo-style free play.
    if stage.number == 0:
        return True
    # Stage 1 becomes available after guidance from stage 0 (always unlockable once user engages).
    if stage.number == 1:
        return True
    if not getattr(user, "is_authenticated", False):
        return False
    previous = ExamStage.objects.filter(track=stage.track, number=stage.number - 1, is_active=True).first()
    if not previous:
        return False
    required = list(previous.exams.filter(is_published=True, counts_for_stage_unlock=True))
    if not required:
        return False
    progress = {p.exam_id: p for p in UserExamProgress.objects.filter(user=user, exam__in=required)}
    passed = sum(1 for exam in required if (progress.get(exam.pk) and progress[exam.pk].status == "completed"))
    return (passed * 100 / len(required)) >= stage.required_completion_percent


def exam_access(user, exam):
    """Return (allowed, reason). Always enforce access on the server."""
    authenticated = bool(getattr(user, "is_authenticated", False))
    if exam.stage_id and not stage_is_unlocked(user, exam.stage):
        return False, "برای باز شدن این مرحله، ابتدا مرحله قبل را تکمیل کن."
    if exam.prerequisite_exam_id:
        if not authenticated:
            return False, "برای بررسی پیش‌نیاز، ابتدا وارد حساب کاربری شو."
        prerequisite = UserExamProgress.objects.filter(user=user, exam=exam.prerequisite_exam).first()
        if not prerequisite or prerequisite.status != "completed":
            return False, f"ابتدا آزمون «{exam.prerequisite_exam.title}» را با حداقل امتیاز لازم تکمیل کن."
        if prerequisite.best_score < exam.prerequisite_exam.minimum_score:
            return False, "حداقل امتیاز آزمون پیش‌نیاز کسب نشده است."
    if exam.access_type == "prerequisite" and not exam.prerequisite_exam_id:
        return False, "پیش‌نیاز این آزمون هنوز در پنل مدیریت تعیین نشده است."
    if exam.access_type == "premium":
        if not authenticated:
            return False, "این آزمون ویژه است؛ ابتدا وارد حساب کاربری شو."
        if not ExamPurchase.objects.filter(user=user, exam=exam, status="paid").exists() and not user.is_superuser:
            return False, "برای دسترسی به این آزمون باید خرید آن ثبت شود."
    return True, "آزمون آماده شروع است."


def track(request,track_code):
    item=get_object_or_404(ExamTrack,code=track_code,is_active=True)
    stages=list(item.stages.filter(is_active=True).prefetch_related("exams__topic").order_by("order","number"))
    progress_map={}
    if request.user.is_authenticated:
        progress_map={p.exam_id:p for p in UserExamProgress.objects.filter(user=request.user,exam__track=item)}
    stage_cards=[]
    for stage in stages:
        exams=[]
        stage_unlocked=stage_is_unlocked(request.user,stage)
        for exam in stage.exams.filter(is_published=True).select_related("topic","prerequisite_exam").order_by("display_order","id"):
            allowed,reason=exam_access(request.user,exam)
            progress=progress_map.get(exam.pk)
            if request.user.is_authenticated and allowed and progress is None:
                progress, _ = UserExamProgress.objects.get_or_create(
                    user=request.user, exam=exam,
                    defaults={"status":"unlocked", "unlocked_at":timezone.now()},
                )
                progress_map[exam.pk]=progress
            elif request.user.is_authenticated and allowed and progress and progress.status=="locked":
                progress.status="unlocked"
                progress.unlocked_at=progress.unlocked_at or timezone.now()
                progress.save(update_fields=["status","unlocked_at","updated_at"])
            status="completed" if progress and progress.status=="completed" else ("unlocked" if allowed else "locked")
            exams.append({"exam":exam,"allowed":allowed,"reason":reason,"progress":progress,"status":status})
        completed=sum(1 for row in exams if row["status"]=="completed")
        stage_cards.append({"stage":stage,"exams":exams,"count":len(exams),"completed":completed,"unlocked":stage_unlocked})
    legacy=[]
    for exam in item.exams.filter(is_published=True,stage__isnull=True).select_related("topic","prerequisite_exam").order_by("id"):
        allowed,reason=exam_access(request.user,exam)
        progress=progress_map.get(exam.pk)
        if request.user.is_authenticated and allowed and progress is None:
            progress, _ = UserExamProgress.objects.get_or_create(
                user=request.user, exam=exam,
                defaults={"status":"unlocked", "unlocked_at":timezone.now()},
            )
            progress_map[exam.pk]=progress
        legacy.append({"exam":exam,"allowed":allowed,"reason":reason,"progress":progress,"status":"completed" if progress and progress.status=="completed" else ("unlocked" if allowed else "locked")})
    topic_insights=[]
    if request.user.is_authenticated:
        totals=defaultdict(lambda: {"total": 0, "correct": 0})
        completed_attempts=Attempt.objects.filter(user=request.user, exam__track=item, status__in=["submitted", "expired"])
        answer_map={(a.attempt_id,a.question_id):a for a in AttemptAnswer.objects.filter(attempt__in=completed_attempts).select_related("selected_choice")}
        for link in AttemptQuestion.objects.filter(attempt__in=completed_attempts).select_related("question__topic"):
            bucket=totals[link.question.topic_id]
            bucket["total"] += 1
            answer=answer_map.get((link.attempt_id,link.question_id))
            if answer and answer.selected_choice_id and answer.selected_choice.is_correct:
                bucket["correct"] += 1
        for topic in item.topics.filter(is_active=True).order_by("order","title"):
            data=totals.get(topic.pk)
            if not data or not data["total"]:
                continue
            score=round(data["correct"]*100/data["total"])
            if score >= 80:
                label, advice = "نقطه قوت", "این مبحث را با مرورهای کوتاه در برنامه نگه دار."
            elif score >= 60:
                label, advice = "نیازمند تثبیت", "چند تمرین هدفمند دیگر انجام بده و سپس آزمون ترکیبی بده."
            else:
                label, advice = "نیازمند تمرین بیشتر", "به پاسخ‌های تشریحی برگرد و تمرین این مبحث را در اولویت بگذار."
            topic_insights.append({"topic":topic,"total":data["total"],"correct":data["correct"],"score":score,"label":label,"advice":advice})
    total=sum(x["count"] for x in stage_cards) + len(legacy)
    completed=sum(x["completed"] for x in stage_cards) + sum(1 for row in legacy if row["status"] == "completed")
    percent=round(completed*100/total) if total else 0

    # User-facing stats for track-hero (dynamic values for the <strong> placeholders)
    strength_topics = []
    weakness_topics = []
    if topic_insights:
        ranked = sorted(topic_insights, key=lambda x: x["score"], reverse=True)
        strength_topics = [x["topic"].title for x in ranked[:3] if x["score"] >= 60]
        weakness_topics = [x["topic"].title for x in sorted(topic_insights, key=lambda x: x["score"])[:3] if x["score"] < 80]

    return render(request, "portal/track.html", {
        "track": item,
        "stage_cards": stage_cards,
        "legacy_exams": legacy,
        "total_exams": total,
        "completed_exams": completed,
        "path_percent": percent,
        "topic_insights": topic_insights,
        "strength_topics": strength_topics,
        "weakness_topics": weakness_topics,
    })

def claim_guest_attempts(request, user):
    """Attach guest attempts from this browser session after sign-up/login."""
    for key, token in list(request.session.items()):
        if not key.startswith("guest_"):
            continue
        try:
            attempt_id = int(key.split("_", 1)[1])
            attempt = Attempt.objects.filter(pk=attempt_id, user__isnull=True, guest_key=token).select_related("exam").first()
        except (TypeError, ValueError):
            attempt = None
        if not attempt:
            continue
        attempt.user = user
        attempt.save(update_fields=["user"])
        if attempt.status in ["submitted", "expired"]:
            progress, _ = UserExamProgress.objects.get_or_create(user=user, exam=attempt.exam)
            progress.attempts_count = Attempt.objects.filter(user=user, exam=attempt.exam, status__in=["submitted", "expired"]).count()
            progress.last_score = attempt.score_percent
            progress.best_score = max(progress.best_score, attempt.score_percent)
            progress.status = "completed" if progress.best_score >= attempt.exam.minimum_score else "unlocked"
            progress.unlocked_at = progress.unlocked_at or attempt.started_at
            if progress.status == "completed": progress.completed_at = progress.completed_at or attempt.submitted_at
            progress.save()


def register(request):
    form=UserCreationForm(request.POST or None)
    if request.method=="POST" and form.is_valid():
        user=form.save(); StudyProfile.objects.get_or_create(user=user); login(request,user); claim_guest_attempts(request,user); return redirect("dashboard")
    return render(request,"portal/auth.html",{"form":form,"heading":"ساخت حساب کاربری","register_mode":True})

def login_view(request):
    form=AuthenticationForm(request,data=request.POST or None)
    if request.method=="POST" and form.is_valid():
        login(request,form.get_user()); claim_guest_attempts(request,form.get_user()); return redirect(request.GET.get("next") or "dashboard")
    return render(request,"portal/auth.html",{"form":form,"heading":"ورود به حساب","register_mode":False})

@require_POST
def logout_view(request):
    logout(request); return redirect("home")

@login_required
def dashboard(request):
    attempts=Attempt.objects.filter(user=request.user,status="submitted").select_related("exam","exam__track")
    total=attempts.count()
    avg=sum((x.score_percent for x in attempts),Decimal("0"))/total if total else Decimal("0")
    profile,_=StudyProfile.objects.get_or_create(user=request.user)
    return render(request,"portal/dashboard.html",{"recent":attempts[:6],"total":total,"average":avg,"profile":profile,"active_count":Attempt.objects.filter(user=request.user,status="active").count()})

@login_required
def profile(request):
    obj,_=StudyProfile.objects.get_or_create(user=request.user)
    if request.method=="POST":
        try: obj.daily_minutes=max(15,min(720,int(request.POST.get("daily_minutes",60))))
        except ValueError: obj.daily_minutes=60
        obj.work_schedule=request.POST.get("work_schedule","")[:30]
        obj.experience_level=request.POST.get("experience_level","")[:30]
        obj.preferred_study_time=request.POST.get("preferred_study_time","")[:20]
        obj.exam_date=request.POST.get("exam_date") or None
        obj.save(); messages.success(request,"پروفایل مطالعاتی ذخیره شد."); return redirect("dashboard")
    return render(request,"portal/profile.html",{"profile":obj})

def owned_attempt(request,pk):
    a=get_object_or_404(Attempt.objects.select_related("exam"),pk=pk)
    if a.user_id:
        if not request.user.is_authenticated or a.user_id!=request.user.id: raise PermissionError
    elif str(a.guest_key)!=request.session.get(f"guest_{a.pk}"): raise PermissionError
    return a

@require_POST
def start_exam(request,track_code,exam_slug):
    t=get_object_or_404(ExamTrack,code=track_code,is_active=True)
    e=get_object_or_404(Exam,track=t,slug=exam_slug,is_published=True)
    allowed, reason = exam_access(request.user, e)
    guest_allowed = (e.access_type == "free" or e.is_free) and e.guest_allowed
    if not request.user.is_authenticated and not guest_allowed:
        messages.info(request, reason if not allowed else "برای ذخیره پیشرفت، ابتدا وارد حساب کاربری شو.")
        return redirect(f"/accounts/login/?next={reverse('track', kwargs={'track_code': t.code})}")
    if not allowed:
        messages.warning(request, reason)
        return redirect("track", track_code=t.code)
    if request.user.is_authenticated:
        progress, _ = UserExamProgress.objects.get_or_create(user=request.user, exam=e)
        if progress.unlocked_at is None:
            progress.unlocked_at = timezone.now()
            if progress.status == "locked": progress.status = "unlocked"
            progress.save(update_fields=["unlocked_at", "status", "updated_at"])
    # A new attempt receives only active questions from active topics.
    # Do not filter in-progress attempts later: their question set must remain stable.
    links=list(
        e.exam_questions.filter(
            question__is_active=True,
            question__topic__is_active=True,
            question__topic__track__is_active=True,
        ).select_related("question", "question__topic").prefetch_related("question__choices").order_by("order", "pk")
    )
    if e.question_limit:
        links=links[:e.question_limit]
    if not links:
        messages.error(request, "برای این آزمون سؤال فعالی اختصاص داده نشده است. از پنل مدیریت، بخش بانک سؤالات ← اختصاص گروهی سؤالات به آزمون را اجرا کن.")
        return redirect("track", track_code=t.code)
    invalid=[]
    for link in links:
        choices=list(link.question.choices.all())
        if len(choices)!=4 or sum(1 for choice in choices if choice.is_correct)!=1:
            invalid.append(link.question_id)
    if invalid:
        messages.error(request, "ساختار گزینه‌های سؤال(های) زیر ناقص است: " + ", ".join(map(str, invalid[:20])))
        return redirect("track", track_code=t.code)
    now=timezone.now()
    with transaction.atomic():
        a=Attempt.objects.create(exam=e,user=request.user if request.user.is_authenticated else None,started_at=now,ends_at=now+timezone.timedelta(minutes=e.duration_minutes))
        AttemptQuestion.objects.bulk_create([
            AttemptQuestion(
                attempt=a,
                question=link.question,
                order=position,
                points=link.points,
            )
            for position, link in enumerate(links, start=1)
        ])
    if not request.user.is_authenticated: request.session[f"guest_{a.pk}"]=str(a.guest_key)
    return redirect("take_exam",attempt_id=a.pk)


def attempt_question_links(a):
    # New attempts use their saved question snapshot. Older attempts created
    # before this feature have no snapshot, so retain their original behavior.
    snapshots = list(
        a.attempt_questions.select_related("question")
        .prefetch_related("question__choices")
        .order_by("order", "pk")
    )
    if snapshots:
        return snapshots
    return list(
        a.exam.exam_questions.select_related("question")
        .prefetch_related("question__choices")
        .order_by("order", "pk")
    )


def take_exam(request,attempt_id):
    try: a=owned_attempt(request,attempt_id)
    except PermissionError: return HttpResponseForbidden("دسترسی مجاز نیست.")
    if a.status!="active": return redirect("result",attempt_id=a.pk)
    if timezone.now()>=a.ends_at: finalize(a,True); return redirect("result",attempt_id=a.pk)
    # Keep the exact question set for an already-started attempt, even if an
    # administrator archives a question while the candidate is answering.
    links=attempt_question_links(a)
    saved={x.question_id:x for x in a.answers.select_related("selected_choice")}
    items=[]
    for n,link in enumerate(links,1):
        ans=saved.get(link.question_id)
        items.append({"number":n,"question":link.question,"choices":link.question.choices.all(),"selected_id":ans.selected_choice_id if ans else None,"marked":ans.marked_for_review if ans else False})
    return render(request,"portal/take_exam.html",{"attempt":a,"items":items,"remaining":max(0,int((a.ends_at-timezone.now()).total_seconds()))})

@require_POST
def save_answer(request,attempt_id):
    try: a=owned_attempt(request,attempt_id)
    except PermissionError: return JsonResponse({"ok":False},status=403)
    if a.status!="active" or timezone.now()>=a.ends_at:
        if a.status=="active": finalize(a,True)
        return JsonResponse({"ok":False,"expired":True},status=409)
    try:
        data=json.loads(request.body); qid=int(data["question_id"]); cid=data.get("choice_id"); marked=bool(data.get("marked",False))
    except (ValueError,KeyError,TypeError,json.JSONDecodeError): return JsonResponse({"ok":False},status=400)
    allowed_ids = [link.question_id for link in attempt_question_links(a)]
    if qid not in allowed_ids: return JsonResponse({"ok":False},status=400)
    choice=get_object_or_404(Choice,pk=cid,question_id=qid) if cid is not None else None
    answer,_=AttemptAnswer.objects.get_or_create(attempt=a,question_id=qid)
    answer.selected_choice=choice; answer.marked_for_review=marked; answer.save()
    return JsonResponse({"ok":True})

def finalize(a,expired=False):
    if a.status!="active": return
    links=attempt_question_links(a)
    answers={x.question_id:x for x in a.answers.select_related("selected_choice")}
    right=wrong=blank=0
    for link in links:
        ans=answers.get(link.question_id)
        if not ans or not ans.selected_choice_id: blank+=1
        elif ans.selected_choice.is_correct: right+=1
        else: wrong+=1
    total=len(links); a.correct_count=right; a.wrong_count=wrong; a.unanswered_count=blank
    a.score_percent=(Decimal(right)*100/total).quantize(Decimal("0.01")) if total else 0
    a.status="expired" if expired else "submitted"; a.submitted_at=timezone.now(); a.save()
    if a.user_id:
        progress, _ = UserExamProgress.objects.get_or_create(user_id=a.user_id, exam=a.exam)
        progress.attempts_count = Attempt.objects.filter(user_id=a.user_id, exam=a.exam, status__in=["submitted", "expired"]).count()
        progress.last_score = a.score_percent
        progress.best_score = max(progress.best_score, a.score_percent)
        progress.status = "completed" if progress.best_score >= a.exam.minimum_score else "unlocked"
        if progress.unlocked_at is None:
            progress.unlocked_at = a.started_at
        if progress.status == "completed" and progress.completed_at is None:
            progress.completed_at = a.submitted_at
        progress.save()

@require_POST
def submit_exam(request,attempt_id):
    try: a=owned_attempt(request,attempt_id)
    except PermissionError: return HttpResponseForbidden("دسترسی مجاز نیست.")
    with transaction.atomic():
        a=Attempt.objects.select_for_update().get(pk=a.pk)
        if a.status=="active": finalize(a,timezone.now()>=a.ends_at)
    return redirect("result",attempt_id=a.pk)

def result(request,attempt_id):
    try: a=owned_attempt(request,attempt_id)
    except PermissionError: return HttpResponseForbidden("دسترسی مجاز نیست.")
    if a.status=="active":
        if timezone.now()>=a.ends_at: finalize(a,True)
        else: return redirect("take_exam",attempt_id=a.pk)
    links=attempt_question_links(a)
    answers={x.question_id:x for x in a.answers.select_related("selected_choice")}
    review=[]
    for link in links:
        q=link.question; ans=answers.get(q.pk); selected=ans.selected_choice if ans else None
        review.append({"question":q,"choices":q.choices.all(),"selected":selected,"is_correct":bool(selected and selected.is_correct)})
    return render(request,"portal/result.html",{"attempt":a,"review":review})
