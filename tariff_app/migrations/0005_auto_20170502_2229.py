# -*- coding: utf-8 -*-
# Generated by Django 1.9 on 2017-05-02 19:29
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tariff_app', '0004_auto_20170416_0350'),
    ]

    operations = [
        migrations.AlterField(
            model_name='tariff',
            name='calc_type',
            field=models.CharField(choices=[('Df', 'Базовый расчётный функционал'), ('Dp', 'IS'), ('Cp', 'Для внутреннего пользования')], default='Df', max_length=2),
        ),
    ]
