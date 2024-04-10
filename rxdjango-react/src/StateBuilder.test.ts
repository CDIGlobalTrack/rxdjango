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

  it('should be created with the correct initial properties', () => {
    expect(stateBuilder).toBeDefined();
    expect(stateBuilder.state).toBeUndefined();
  });

  it('anchor is received', () => {
    const instance = { name: 'Project #1', ...header('ProjectSerializer', 1) } as ProjectType;
    const instances = [instance];
    stateBuilder.update(instances);
    expect(stateBuilder.state!.id).toBe(1);
    expect(stateBuilder.state!.name).toBe('Project #1');
  });

  it('empty list of related objects is preserved', () => {
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

  it('related objects are initialized as unloaded object', () => {
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

  it('related object is initialized', () => {
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

});
