from django.db import migrations, models
from django.db.models import Q
import django.core.validators


class Migration(migrations.Migration):
    dependencies = [
        ("portal", "0004_alter_question_source_year_alter_topic_slug"),
    ]

    operations = [
        migrations.AlterField(
            model_name="examtrack",
            name="code",
            field=models.SlugField(
                choices=[("execution", "اجرا"), ("supervision", "نظارت")],
                max_length=30,
                unique=True,
            ),
        ),
        migrations.AlterField(
            model_name="topic",
            name="slug",
            field=models.CharField(max_length=255, help_text="شناسه منبع؛ متن یا عدد"),
        ),
        migrations.AlterField(
            model_name="question",
            name="source_year",
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                validators=[django.core.validators.MinValueValidator(1381)],
            ),
        ),
        migrations.AddConstraint(
            model_name="examtrack",
            constraint=models.CheckConstraint(
                condition=Q(code__in=["execution", "supervision"]),
                name="valid_exam_track_code",
            ),
        ),
        migrations.AddConstraint(
            model_name="question",
            constraint=models.CheckConstraint(
                condition=Q(source_year__isnull=True) | Q(source_year__gt=1380),
                name="question_source_year_after_1380",
            ),
        ),
    ]
