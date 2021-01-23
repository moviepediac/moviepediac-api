# Generated by Django 3.1.4 on 2021-01-23 09:46

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0007_auto_20210118_2237"),
    ]

    operations = [
        migrations.RenameField(
            model_name="topcurator",
            old_name="recommend_count",
            new_name="likes_on_recommend",
        ),
        migrations.AddField(
            model_name="topcurator",
            name="score",
            field=models.FloatField(default=0),
        ),
    ]
