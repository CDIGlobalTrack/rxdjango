from rxdjango.channels import StateChannel
from .models import Room
from
from .serializers import RoomSerializer


class RoomChannel(StateChannel):

    class Meta:
        state = RoomSerializer()

    @staticmethod
    def has_permission(user, room_id):
        try:
            user.rooms.get(id=room_id)
            return True
        except Room.DoesNotExist:
            return False
