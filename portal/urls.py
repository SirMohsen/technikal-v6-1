from django.urls import path
from . import views
urlpatterns=[
 path("<slug:track_code>/",views.track,name="track"),
 path("<slug:track_code>/<slug:exam_slug>/start/",views.start_exam,name="start_exam"),
 path("attempt/<int:attempt_id>/",views.take_exam,name="take_exam"),
 path("attempt/<int:attempt_id>/save/",views.save_answer,name="save_answer"),
 path("attempt/<int:attempt_id>/submit/",views.submit_exam,name="submit_exam"),
 path("attempt/<int:attempt_id>/result/",views.result,name="result"),
]
