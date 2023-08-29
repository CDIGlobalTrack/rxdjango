from react_framework.channels import StateChannel
from .serializers import JobNestedSerializer


class JobStateChannel(StateChannel):

    class Meta:
        anchor = JobNestedSerializer()
