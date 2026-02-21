from django.contrib import admin
from .models import Permission, Role, UserProfile

# Register your models here.

@admin.register(Permission)
class PermissionAdmin(admin.ModelAdmin):
    list_display = ['key', 'name', 'module']

@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ['name', 'description']
    filter_horizontal = ['permissions']

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'status']
    filter_horizontal = ['roles']
