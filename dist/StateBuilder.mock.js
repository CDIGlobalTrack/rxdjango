export const ANCHOR = 'project.serializers.ProjectSerializer';
export const MODEL = {
    'project.serializers.ProjectSerializer': {
        'customer': 'project.serializers.CustomerSerializer',
        'tasks': 'project.serializers.TaskSerializer',
    },
    'project.serializers.TaskSerializer': {
        'user': 'project.serializers.UserSerializer',
    },
    'project.serializers.CustomerSerializer': {
        'tasks': 'project.serializers.TaskSerializer',
    },
    'project.serializers.UserSerializer': {},
};
