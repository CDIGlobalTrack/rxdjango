import StateBuilder from './StateBuilder';
import { TempInstance } from './ContextChannel.interfaces';
import {
  ProjectType,
  ANCHOR,
  MODEL,
  ProjectTestData,
  CustomerTestData,
  TaskTestData,
  UserType,
} from './StateBuilder.mock'

const header = (
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
    stateBuilder = new StateBuilder<ProjectType>(MODEL, ANCHOR, false);
  });

  it('is initialized with an undefined state', () => {
    expect(stateBuilder).toBeDefined();
    expect(stateBuilder.state).toBeUndefined();
  });

  it('creates state with anchor data when it is received', () => {
    const instance = { projectName: 'Project #1', ...header('ProjectSerializer', 1) } as ProjectType;
    const instances = [instance];
    stateBuilder.update(instances);
    const state: ProjectType = stateBuilder.state! as ProjectType;
    expect(state!.id).toBe(1);
    expect(state!.projectName).toBe('Project #1');
  });

  it('initializes related sets with empty lists when there are no related instances', () => {
    const instances = [{
        ...header('ProjectSerializer', 1),
        projectName: 'Project #1',
        tasks: [],
      },
    ];

    stateBuilder.update(instances as TempInstance[]);
    const state: ProjectType = stateBuilder.state! as ProjectType;
    expect(state!.id).toBe(1);
    expect(state!.projectName).toBe('Project #1');
    expect(state!.tasks).toEqual([]);
  });

  it('initializes related sets with UnloadedInstance objects', () => {
    const instance = { projectName: 'Project #1', tasks: [1, 2, 3], ...header('ProjectSerializer', 1) };
    const instances = [instance];

    stateBuilder.update(instances as TempInstance[]);
    const state: ProjectType = stateBuilder.state! as ProjectType;
    expect(state?.tasks?.[0]._loaded).toEqual(false);
    expect(state?.tasks?.[0].id).toEqual(1);
    expect(state?.tasks?.[1]._loaded).toEqual(false);
    expect(state?.tasks?.[1].id).toEqual(2);
    expect(state?.tasks?.[2]._loaded).toEqual(false);
    expect(state?.tasks?.[2].id).toEqual(3);
  });

  it('loads related sets when data is received', () => {
    const projectInstance: ProjectTestData = {
      ...header('ProjectSerializer', 1),
      projectName: 'Project #1',
      tasks: [1, 2, 3],
    };
    const taskInstance: TaskTestData = {
      ...header('TaskSerializer', 1),
      taskName: 'Task #1',
    };

    stateBuilder.update([projectInstance]);
    stateBuilder.update([taskInstance]);
    const state: ProjectType = stateBuilder.state! as ProjectType;
    expect(state!.tasks?.[0].id).toEqual(taskInstance.id);
    expect(state!.tasks?.[0].taskName).toEqual(taskInstance.taskName);
    expect(state!.tasks?.[0]._loaded).toEqual(true);
    expect(state!.tasks?.[1].id).toEqual(2);
    expect(state!.tasks?.[1].taskName).toEqual(undefined);
    expect(state!.tasks?.[1]._loaded).toEqual(false);
  });

  it('changes the object reference when it is loaded', () => {
    const projectInstance: ProjectTestData = {
      ...header('ProjectSerializer', 1),
      projectName: 'Project #1',
      tasks: [1, 2, 3],
    };
    const taskInstance: TaskTestData = {
      ...header('TaskSerializer', 1),
      taskName: 'Task #1',
    };
    stateBuilder.update([projectInstance]);
    const task = (stateBuilder.state! as ProjectType).tasks![0];
    expect(task._loaded).toEqual(false);
    stateBuilder.update([taskInstance]);
    const newTask = (stateBuilder.state! as ProjectType).tasks![0];
    expect(newTask).not.toBe(task);
    expect(newTask._loaded).toEqual(true);

    taskInstance.taskName = 'changed';
    stateBuilder.update([taskInstance]);
    expect((stateBuilder.state! as ProjectType).tasks![0]).not.toBe(newTask);
  });

  it('changes the middle node reference when child node is updated', () => {
    const projectInstance: ProjectTestData = {
      ...header('ProjectSerializer', 1),
      projectName: 'Project #1',
      customer: 2,
    };

    const customerInstance: CustomerTestData = {
      ...header('CustomerSerializer', 2),
      customerName: 'Customer #2',
      tasks: [1, 2, 3],
    };

    const task1: TaskTestData = {
      ...header('TaskSerializer', 1),
      taskName: 'Task #1',
    };

    const task2: TaskTestData = {
      ...header('TaskSerializer', 2),
      taskName: 'Task #2',
    };

    stateBuilder.update([projectInstance]);
    stateBuilder.update([customerInstance]);
    stateBuilder.update([task1]);
    let state: ProjectType = stateBuilder.state! as ProjectType;

    const customer = state!.customer;
    const tasks = state!.customer!.tasks;
    stateBuilder.update([task2]);
    state = stateBuilder.state! as ProjectType;
    expect(state!.customer).not.toBe(customer);
    expect(state!.customer!.tasks).not.toBe(tasks);
  });

  it('changes the set reference if some child instance changes', () => {
    const projectInstance: ProjectTestData = {
      ...header('ProjectSerializer', 1),
      projectName: 'Project #1',
      customer: 2,
      tasks: [1]
    };

    const customerInstance: CustomerTestData = {
      ...header('CustomerSerializer', 2),
      customerName: 'Customer #2',
      tasks: [1],
    };

    const userInstance: UserType = {
      ...header('UserSerializer', 1),
      username: 'User #1',
    };

    const taskInstance: TaskTestData = {
      ...header('TaskSerializer', 1),
      taskName: 'Task #1',
      user: 1,
    };

    stateBuilder.update([projectInstance]);
    stateBuilder.update([customerInstance]);
    stateBuilder.update([taskInstance]);

    const project = stateBuilder.state;
    let state: ProjectType = stateBuilder.state! as ProjectType;

    const projectTasks = state!.tasks;
    const customer = state!.customer;
    const customerTasks = state!.customer!.tasks;
    const user = projectTasks[0].user;

    stateBuilder.update([userInstance]);
    state = stateBuilder.state! as ProjectType;
    expect(project).not.toBe(state);
    expect(state!.tasks).not.toBe(projectTasks);
    expect(state!.tasks[0].user).not.toBe(user);
    expect(state!.customer).not.toBe(customer);
    expect(state!.customer!.tasks).not.toBe(customerTasks);
  });

  it('initializes foreign key with unloaded object', () => {
    const projectInstance: ProjectTestData = {
      ...header('ProjectSerializer', 1),
      projectName: 'Project #1',
      customer: 1,
    };
    stateBuilder.update([projectInstance]);
    const state: ProjectType = stateBuilder.state! as ProjectType;
    expect(state?.customer._loaded).toEqual(false);
    expect(state?.customer.id).toEqual(1);
  });

  it('loads foreign key when it arrives, changing reference', () => {
    const projectInstance: ProjectTestData = {
      ...header('ProjectSerializer', 1),
      projectName: 'Project #1',
      customer: 5,
    };
    const customerInstance: CustomerTestData = {
      ...header('CustomerSerializer', 5),
      customerName: 'Customer #5',
    };
    stateBuilder.update([projectInstance]);
    let  state: ProjectType = stateBuilder.state! as ProjectType;
    const customer = state?.customer
    stateBuilder.update([customerInstance]);
    state = stateBuilder.state! as ProjectType;
    expect(state?.customer._loaded).toEqual(true);
    expect(state?.customer.id).toEqual(5);
    expect(state?.customer).not.toBe(customer);
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
    const state: ProjectType = stateBuilder.state! as ProjectType;
    const customer = state.customer!;
    expect(state.tasks[2]).toBe(customer.tasks[0]);
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
    const state: ProjectType = stateBuilder.state! as ProjectType;
    const customer = state.customer!;
    expect(state.tasks[2]).toBe(customer.tasks[0]);
  });

  it('deletes parent reference when object is deleted', () => {
    const projectInstance: ProjectTestData = {
      ...header('ProjectSerializer', 1),
      projectName: 'Project #1',
      tasks: [1, 2, 3],
    };
    const taskInstances: TaskTestData[] = [{
      ...header('TaskSerializer', 1),
      taskName: 'Task #1',
    }, {
      ...header('TaskSerializer', 2),
      taskName: 'Task #2',
    }, {
      ...header('TaskSerializer', 3),
      taskName: 'Task #3',
    }];

    stateBuilder.update([projectInstance]);
    stateBuilder.update(taskInstances);

    const del = header('TaskSerializer', 2, 'delete');
    stateBuilder.update([del]);
    const state = stateBuilder.state! as ProjectType
    expect(state.tasks.length).toBe(2);
    expect(state.tasks[0].id).toBe(1);
    expect(state.tasks[1].id).toBe(3);
  });

});

describe('StateBuilder removeTempInstance preserves .create()', () => {
  const TASK_TYPE = 'project.serializers.TaskSerializer';
  const PROJECT_TYPE = 'project.serializers.ProjectSerializer';

  const writable = {
    [TASK_TYPE]: ['create', 'save', 'delete'] as const,
  };

  const writeCallbacks = {
    saveInstance: jest.fn().mockResolvedValue(undefined),
    createInstance: jest.fn().mockResolvedValue(-1),
    deleteInstance: jest.fn().mockResolvedValue(undefined),
  };

  it('relation array has .create() after removeTempInstance rollback', () => {
    const stateBuilder = new StateBuilder<ProjectType>(
      MODEL, ANCHOR, false, writable, writeCallbacks,
    );

    // Load a project with one task
    stateBuilder.update([{
      id: 1,
      _instance_type: PROJECT_TYPE,
      _operation: 'create',
      _tstamp: 1,
      projectName: 'Project #1',
      tasks: [1],
      customer: null,
    } as any]);

    stateBuilder.update([{
      id: 1,
      _instance_type: TASK_TYPE,
      _operation: 'create',
      _tstamp: 1,
      taskName: 'Task #1',
    } as any]);

    // Verify .create() is attached to the tasks array
    let state = stateBuilder.state! as ProjectType;
    expect((state.tasks as any).create).toBeDefined();

    // Simulate optimistic create (adds temp instance with negative id)
    stateBuilder.addTempInstance(TASK_TYPE, -1, PROJECT_TYPE, 1, 'tasks', {
      taskName: 'Temp Task',
    });

    state = stateBuilder.state! as ProjectType;
    expect(state.tasks.length).toBe(2);

    // Simulate rollback: server rejected, remove temp instance
    stateBuilder.removeTempInstance(TASK_TYPE, -1);

    state = stateBuilder.state! as ProjectType;
    expect(state.tasks.length).toBe(1);

    // BUG: .create() should still be on the array after rollback
    expect((state.tasks as any).create).toBeDefined();
  });

  it('relation array has .create() after applyOptimisticDelete rollback', () => {
    const stateBuilder = new StateBuilder<ProjectType>(
      MODEL, ANCHOR, false, writable, writeCallbacks,
    );

    // Load a project with two tasks
    stateBuilder.update([{
      id: 1,
      _instance_type: PROJECT_TYPE,
      _operation: 'create',
      _tstamp: 1,
      projectName: 'Project #1',
      tasks: [1, 2],
      customer: null,
    } as any]);

    stateBuilder.update([
      {
        id: 1,
        _instance_type: TASK_TYPE,
        _operation: 'create',
        _tstamp: 1,
        taskName: 'Task #1',
      } as any,
      {
        id: 2,
        _instance_type: TASK_TYPE,
        _operation: 'create',
        _tstamp: 1,
        taskName: 'Task #2',
      } as any,
    ]);

    // Verify .create() is attached
    let state = stateBuilder.state! as ProjectType;
    expect((state.tasks as any).create).toBeDefined();

    // Simulate optimistic delete
    stateBuilder.applyOptimisticDelete(TASK_TYPE, 2);

    state = stateBuilder.state! as ProjectType;
    expect(state.tasks.length).toBe(1);

    // BUG: .create() should still be on the array after optimistic delete
    expect((state.tasks as any).create).toBeDefined();
  });
});
