import StateBuilder from './StateBuilder';
import { TempInstance } from './StateChannel.d';
import {
  ProjectType,
  ANCHOR,
  MODEL,
  ProjectPayload,
  CustomerPayload,
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
    const instance = { projectName: 'Project #1', ...header('ProjectSerializer', 1) } as ProjectType;
    const instances = [instance];
    stateBuilder.update(instances);
    expect(stateBuilder.state!.id).toBe(1);
    expect(stateBuilder.state!.projectName).toBe('Project #1');
  });

  it('initializes related sets with empty lists when there are no related instances', () => {
    const instances = [{
        ...header('ProjectSerializer', 1),
        projectName: 'Project #1',
        tasks: [],
      },
    ];

    stateBuilder.update(instances as TempInstance[]);
    expect(stateBuilder.state!.id).toBe(1);
    expect(stateBuilder.state!.projectName).toBe('Project #1');
    expect(stateBuilder.state!.tasks).toEqual([]);
  });

  it('initializes related sets with UnloadedInstance objects', () => {
    const instance = { projectName: 'Project #1', tasks: [1, 2, 3], ...header('ProjectSerializer', 1) };
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
      projectName: 'Project #1',
      tasks: [1, 2, 3],
    };
    const taskInstance: TaskPayload = {
      ...header('TaskSerializer', 1),
      taskName: 'Task #1',
    };

    stateBuilder.update([projectInstance]);
    stateBuilder.update([taskInstance]);
    expect(stateBuilder.state!.tasks?.[0].id).toEqual(taskInstance.id);
    expect(stateBuilder.state!.tasks?.[0].taskName).toEqual(taskInstance.taskName);
    expect(stateBuilder.state!.tasks?.[0]._loaded).toEqual(true);
    expect(stateBuilder.state!.tasks?.[1].id).toEqual(2);
    expect(stateBuilder.state!.tasks?.[1].taskName).toEqual(undefined);
    expect(stateBuilder.state!.tasks?.[1]._loaded).toEqual(false);
  });

  it('preserves the object reference when it is loaded', () => {
    const projectInstance: ProjectPayload = {
      ...header('ProjectSerializer', 1),
      projectName: 'Project #1',
      tasks: [1, 2, 3],
    };
    const taskInstance: TaskPayload = {
      ...header('TaskSerializer', 1),
      taskName: 'Task #1',
    };

    stateBuilder.update([projectInstance]);
    const task = stateBuilder.state!.tasks![0];
    expect(task._loaded).toEqual(false);
    stateBuilder.update([taskInstance]);
    expect(stateBuilder.state!.tasks![0]).toBe(task);
    expect(task._loaded).toEqual(true);
  });

  it('initializes foreign key with unloaded object', () => {
    const projectInstance: ProjectPayload = {
      ...header('ProjectSerializer', 1),
      projectName: 'Project #1',
      customer: 1,
    };
    stateBuilder.update([projectInstance]);
    expect(stateBuilder.state?.customer._loaded).toEqual(false);
    expect(stateBuilder.state?.customer.id).toEqual(1);
  });

  it('loads foreign key when it arrives, preserving reference', () => {
    const projectInstance: ProjectPayload = {
      ...header('ProjectSerializer', 1),
      projectName: 'Project #1',
      customer: 5,
    };
    const customerInstance: CustomerPayload = {
      ...header('CustomerSerializer', 5),
      customerName: 'Customer #5',
    };
    stateBuilder.update([projectInstance]);
    const customer = stateBuilder.state?.customer
    stateBuilder.update([customerInstance]);
    expect(stateBuilder.state?.customer._loaded).toEqual(true);
    expect(stateBuilder.state?.customer.id).toEqual(5);
    expect(stateBuilder.state?.customer).toBe(customer);
  });

  it('shares the reference of instance when it appears twice, with tasks arriving later', () => {
    stateBuilder.update([{
      ...header('ProjectSerializer', 1),
      projectName: 'Project #1',
      customer: 1,
      tasks: [1, 2, 3],
    }, {
      ...header('CustomerSerializer', 1),
      customerName: 'Customer #1',
      tasks: [3, 4, 5],
    }, {
      ...header('TaskSerializer', 3),
      taskName: 'Task #3',
    }])

    const st = stateBuilder.state!;
    const customer = st.customer!;
    expect(st.tasks[2]).toBe(customer.tasks[0]);
  });

  it('shares the reference of instance when it appears twice, with tasks arriving early', () => {
    stateBuilder.update([{
      ...header('ProjectSerializer', 1),
      projectName: 'Project #1',
      customer: 1,
      tasks: [1, 2, 3],
    }, {
      ...header('TaskSerializer', 3),
      taskName: 'Task #3',
    }, {
      ...header('CustomerSerializer', 1),
      customerName: 'Customer #1',
      tasks: [3, 4, 5],
    }])

    const st = stateBuilder.state!;
    const customer = st.customer!;
    expect(st.tasks[2]).toBe(customer.tasks[0]);
  });

});
