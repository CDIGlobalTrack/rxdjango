import StateBuilder from './StateBuilder';
import { TempInstance } from './StateChannel.d';
import {
  ProjectType,
  ANCHOR,
  MODEL,
  ProjectPayload,
  TaskPayload,
} from './StateBuilder.mock'

const header = <T>(
  instanceType: string,
  id: number,
  operation: string = 'create',
) => {
  return {
    id: id,
    '_instance_type': `project.serializers.${instanceType}`,
    '_operation': operation,
    '_tstamp': new Date().getTime(),
  };
};

describe('StateBuilder', () => {
  let stateBuilder: StateBuilder<ProjectType>;

  beforeEach(() => {
    stateBuilder = new StateBuilder<ProjectType>(MODEL, ANCHOR);
  });

  it('is initialized with an undefined state', () => {
    expect(stateBuilder).toBeDefined();
    expect(stateBuilder.state).toBeUndefined();
  });

  it('creates state with anchor data when it is received', () => {
    const instance = { name: 'Project #1', ...header('ProjectSerializer', 1) } as ProjectType;
    const instances = [instance];
    stateBuilder.update(instances);
    expect(stateBuilder.state!.id).toBe(1);
    expect(stateBuilder.state!.name).toBe('Project #1');
  });

  it('initializes related sets with empty lists when there are no related instances', () => {
    const instances = [{
        ...header('ProjectSerializer', 1),
        name: 'Project #1',
        tasks: [],
      },
    ];

    stateBuilder.update(instances as TempInstance[]);
    expect(stateBuilder.state!.id).toBe(1);
    expect(stateBuilder.state!.name).toBe('Project #1');
    expect(stateBuilder.state!.tasks).toEqual([]);
  });

  it('initializes related sets with UnloadedInstance objects', () => {
    const instance = { name: 'Project #1', tasks: [1, 2, 3], ...header('ProjectSerializer', 1) };
    const instances = [instance];

    stateBuilder.update(instances as TempInstance[]);

    expect(stateBuilder.state?.tasks?.[0]._loaded).toEqual(false);
    expect(stateBuilder.state?.tasks?.[0].id).toEqual(1);
    expect(stateBuilder.state?.tasks?.[1]._loaded).toEqual(false);
    expect(stateBuilder.state?.tasks?.[1].id).toEqual(2);
    expect(stateBuilder.state?.tasks?.[2]._loaded).toEqual(false);
    expect(stateBuilder.state?.tasks?.[2].id).toEqual(3);
  });

  it('loads related sets when data is received', () => {
    const projectInstance: ProjectPayload = {
      ...header('ProjectSerializer', 1),
      name: 'Project #1',
      tasks: [1, 2, 3],
    };
    const taskInstance: TaskPayload = {
      ...header('TaskSerializer', 1),
      name: 'Task #1',
    };

    stateBuilder.update([projectInstance]);
    stateBuilder.update([taskInstance]);
    expect(stateBuilder.state!.tasks?.[0].id).toEqual(taskInstance.id);
    expect(stateBuilder.state!.tasks?.[0].name).toEqual(taskInstance.name);
    expect(stateBuilder.state!.tasks?.[0]._loaded).toEqual(true);
    expect(stateBuilder.state!.tasks?.[1].id).toEqual(2);
    expect(stateBuilder.state!.tasks?.[1].name).toEqual(undefined);
    expect(stateBuilder.state!.tasks?.[1]._loaded).toEqual(false);
  });

  it('preserves the object reference when it is loaded', () => {
    const projectInstance: ProjectPayload = {
      ...header('ProjectSerializer', 1),
      name: 'Project #1',
      tasks: [1, 2, 3],
    };
    const taskInstance: TaskPayload = {
      ...header('TaskSerializer', 1),
      name: 'Task #1',
    };

    stateBuilder.update([projectInstance]);
    const task = stateBuilder.state!.tasks![0];
    expect(task._loaded).toEqual(false);
    stateBuilder.update([taskInstance]);
    expect(stateBuilder.state!.tasks![0]).toBe(task);
    expect(task._loaded).toEqual(true);
  });

});
