from rxdjango.channels import StateChannel
from .serializers import JobNestedSerializer


class JobStateChannel(StateChannel):

    class Meta:
        anchor = JobNestedSerializer()
