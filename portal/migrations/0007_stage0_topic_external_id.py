from django.db import migrations, models
import django.db.models


class Migration(migrations.Migration):

    dependencies = [
        ("portal", "0006_remove_examtrack_valid_exam_track_code_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="examstage",
            name="number",
            field=models.PositiveSmallIntegerField(
                choices=[
                    (0, "مرحله صفرم"),
                    (1, "مرحله اول"),
                    (2, "مرحله دوم"),
                    (3, "مرحله سوم"),
                    (4, "مرحله چهارم"),
                    (5, "مرحله پنجم"),
                ]
            ),
        ),
        migrations.AlterField(
            model_name="examstage",
            name="order",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="topic",
            name="external_id",
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text="شناسه یکتای مبحث از فایل اکسل (ای دی مبحث). بانک سوالات بر اساس این شناسه ساخته می‌شود و به هیچ آزمونی وابسته نیست.",
                max_length=64,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="topic",
            name="chapter_number",
            field=models.CharField(blank=True, help_text="شماره فصل (متنی)", max_length=50),
        ),
        migrations.AddField(
            model_name="topic",
            name="section_number",
            field=models.CharField(blank=True, help_text="شماره بند (متنی)", max_length=50),
        ),
        migrations.AddConstraint(
            model_name="topic",
            constraint=models.UniqueConstraint(
                condition=django.db.models.Q(external_id__isnull=False) & ~django.db.models.Q(external_id=""),
                fields=("track", "external_id"),
                name="uniq_topic_external_id_track",
            ),
        ),
        migrations.AddIndex(
            model_name="topic",
            index=models.Index(fields=["external_id"], name="portal_topi_externa_idx"),
        ),
    ]
