from django.contrib import admin
from django.urls import path
from react_test.channels import JobContextChannel

urlpatterns = [
    path('admin/', admin.site.urls),
]

websocket_urlpatterns = [
    path('ws/job/<int:job_id>/', JobContextChannel.as_asgi()),
]
