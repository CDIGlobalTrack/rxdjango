
.. _optimistic-updates:

=======================
Optimistic Updates
=======================

RxDjango supports client-side write operations with optimistic updates,
providing instant UI feedback before the server confirms the operation.
This matches Django's ORM interface for a familiar developer experience.

.. contents:: Table of Contents
   :local:
   :depth: 2


Overview
========

When a user modifies data in the UI, changes are applied immediately
(optimistic update), then reconciled when the server broadcast arrives.
If the server rejects the operation, the client rolls back to the
previous state.

This provides a snappy, responsive user experience even on slow
network connections, while maintaining data consistency through
server-side validation.


Backend Setup
=============

Declaring Writable Types
------------------------

To enable write operations, declare which serializer types are writable
in ``Meta.writable``. This tells the frontend to attach ``.save()``,
``.create()``, and ``.delete()`` methods directly on the state objects
built by the channel.

.. code-block:: python

   from rxdjango.channels import ContextChannel
   from rxdjango.operations import SAVE, CREATE, DELETE
   from myapp.serializers import JobNestedSerializer, TaskSerializer

   class JobContextChannel(ContextChannel):
       class Meta:
           state = JobNestedSerializer()
           writable = {
               TaskSerializer: [SAVE, CREATE, DELETE],
           }

The keys are serializer classes (the same ones used in the state tree).
The values are lists of allowed operations:

- ``SAVE`` — instances of this type get a ``.save(data)`` method
- ``CREATE`` — relation arrays of this type get a ``.create(data)`` method
- ``DELETE`` — instances of this type get a ``.delete()`` method

This declaration is enforced on **both** sides:

- **Frontend**: ``makefrontend`` generates a ``writable`` property that
  ``StateBuilder`` uses to attach write methods during state construction.
  Types not declared do not get write methods.
- **Backend**: The server rejects any write operation whose instance type
  and operation are not declared in ``Meta.writable``, returning a 403
  before any database access occurs.

Types not listed in ``Meta.writable`` are read-only. Operations not listed
for a type are denied even if ``can_*`` would return ``True``.


Authorization Methods
---------------------

All write operations also require explicit authorization. By default, all
operations are denied. Override ``can_*`` methods in your ``ContextChannel``
to enable write operations at runtime:

.. code-block:: python

   class JobContextChannel(ContextChannel):
       class Meta:
           state = JobNestedSerializer()
           writable = {
               TaskSerializer: [SAVE, CREATE, DELETE],
           }

       def can_save(self, instance, data):
           """Allow users to update their own objects."""
           return instance.owner_id == self.user.id

       def can_create(self, model_class, parent, data):
           """Allow users to create children under their objects."""
           return parent.owner_id == self.user.id

       def can_delete(self, instance):
           """Allow users to delete their own objects."""
           return instance.owner_id == self.user.id

``Meta.writable`` declares which types and operations are allowed (enforced
on both frontend and backend). The ``can_*`` methods provide additional
runtime authorization for business logic (ownership, roles, etc.).

**Important**: Both layers must permit the operation. ``Meta.writable``
must list the type and operation, **and** the corresponding ``can_*``
method must return ``True``. Authorization methods default to ``False``
(deny all) — you must explicitly override them.


can_save(instance, data)
------------------------

Check if the current user may update an existing instance.

:param instance: The database instance (pre-update state)
:param data: Partial field dict being applied
:returns: ``True`` to allow, ``False`` to deny

The ``instance`` parameter is loaded from the database and represents
the current state before the update is applied. The ``data`` parameter
contains only the fields being changed.

.. code-block:: python

   def can_save(self, instance, data):
       # Only allow updating tasks assigned to the user
       if 'status' in data:
           return instance.assignee_id == self.user.id
       return False


can_create(model_class, parent, data)
-------------------------------------

Check if the current user may create a new child instance.

:param model_class: The child model being instantiated
:param parent: The parent instance that owns the relation
:param data: Field dict from the frontend
:returns: ``True`` to allow, ``False`` to deny

The ``parent`` is the instance that owns the relation (e.g., the Job
for a new Task). Use this to verify the user has permission to add
children to this specific parent.

.. code-block:: python

   def can_create(self, model_class, parent, data):
       # Only project members can create tasks
       return self.user in parent.project.members.all()


can_delete(instance)
--------------------

Check if the current user may delete an existing instance.

:param instance: The database instance to delete
:returns: ``True`` to allow, ``False`` to deny

.. code-block:: python

   def can_delete(self, instance):
       # Only admins or owners can delete
       return self.user.is_admin or instance.owner_id == self.user.id


Execution Flow
==============

1. Client sends a ``write`` message with operation type and data
2. Server checks ``Meta.writable`` — rejects (403) if the type or operation
   is not declared
3. Server checks the MongoDB cache — rejects (400) if the target instance
   (or parent, for create) does not belong to the channel's current anchor
   context
4. Server loads the required ORM instance(s) from database
5. Server calls the appropriate ``can_*`` method for authorization
6. If denied: sends error response (403), client rolls back
7. If allowed: executes the ORM operation (``save()``, ``create()``, ``delete()``)
8. Django signals fire and broadcast canonical state to all clients
9. Client receives broadcast and reconciles optimistic state

Steps 2 and 3 are security checks that run before any database write.
Step 2 ensures the channel developer has explicitly opted in to this
operation. Step 3 prevents cross-context writes — a client connected to
one anchor cannot modify instances belonging to a different anchor.


Frontend Usage
==============

When ``Meta.writable`` is declared, the state objects from the channel
automatically have write methods attached. No manual wrapping needed.

.. code-block:: typescript

   // State objects have .save() and .delete() attached automatically
   await task.save({ name: 'Updated Name' });
   await task.delete();

   // Relation arrays have .create() attached automatically
   await job.tasks.create({ name: 'New Task', developer: developerId });

   // create() data parameter is optional (useful when backend sets defaults)
   await job.tasks.create();

This mirrors Django's ORM interface. The optimistic update is applied
instantly, and the server confirms or rolls back asynchronously.
The returned promise resolves to the temporary negative ID for the
optimistic instance; the canonical object arrives later via the normal
channel state broadcast.


Writable Type Helpers
---------------------

Generated serializer interfaces remain plain data shapes. Writability is
added at the channel level with the helper types
``Saveable<T, P>``, ``Deleteable<T>``, and ``Creatable<T, P>``:

.. code-block:: typescript

   type WritableTask = Saveable<Deleteable<TaskType>, TaskPayload>;
   type JobState = Omit<JobType, 'tasks'> & {
       tasks: Creatable<WritableTask, TaskPayload>;
   };

At runtime, ``StateBuilder`` attaches ``.save()``, ``.delete()``, and
``.create()`` only when the channel has ``writable`` configured.

When constructing mock data in tests, keep the interface itself as plain
data and only cast to the channel-specific state type when the test needs
write helpers:

.. code-block:: typescript

   const mockJob: JobType = {
       ...baseFields,
       tasks: [mockTask],
   };

   const writableJob = mockJob as JobState;
   await writableJob.tasks.create({ name: 'New Task' });


Low-Level API
-------------

The ``ContextChannel`` also provides low-level write methods for cases
where you need direct control over the parameters. Note that the backend
still enforces ``Meta.writable`` — the type and operation must be declared:

.. code-block:: typescript

   // Save an existing instance
   await channel.saveInstance(
       'myapp.serializers.TaskSerializer',
       taskId,
       { name: 'Updated Name' }
   );

   // Create a new child instance
   await channel.createInstance(
       'myapp.serializers.TaskSerializer',
       'myapp.serializers.JobSerializer',
       jobId,
       'tasks',
       { name: 'New Task', developer: developerId }
   );

   // Delete an instance
   await channel.deleteInstance(
       'myapp.serializers.TaskSerializer',
       taskId
   );


React Integration
-----------------

With ``Meta.writable``, write methods are available directly on state
objects. ``.create()`` returns the temporary negative ID for the
optimistic instance; read the created object from channel state when the
server broadcast arrives:

.. code-block:: typescript

   function TaskEditor({ task }: Props) {
       const [name, setName] = useState(task.name);

       const handleSave = async () => {
           await task.save({ name });
       };

       return (
           <div>
               <input value={name} onChange={e => setName(e.target.value)} />
               <button onClick={handleSave}>Save</button>
           </div>
       );
   }

   function TaskList({ job }: Props) {
       const handleCreate = async () => {
           await job.tasks.create({ name: 'New Task', developer: devId });
       };

       return (
           <div>
               {job.tasks.map(task => <TaskEditor key={task.id} task={task} />)}
               <button onClick={handleCreate}>Add Task</button>
           </div>
       );
   }


Rollback Behavior
=================

When the server rejects an operation, the client automatically rolls back:

save
   Restores previous field values

create
   Removes the temporary instance from the list

delete
   Re-inserts the instance at its original position

The server always broadcasts the canonical state after a successful
operation, so the optimistic value is quietly replaced without visible
flicker.


Temporary IDs
=============

For ``create()`` operations, the client generates a temporary negative
integer ID (e.g., ``-1``, ``-2``) to allow React to render the instance
before the server responds. The temporary entry is replaced with the
real instance when the server broadcast arrives.

.. code-block:: typescript

   // Temporary ID is returned immediately
   const tempId = await channel.createInstance(...);  // Returns -1, -2, etc.

   // Later, the broadcast replaces it with the real ID
   // The UI updates seamlessly


Error Handling
==============

All write methods return promises that reject on error:

.. code-block:: typescript

   try {
       await writableTask.save({ name: 'Updated' });
   } catch (error) {
       if (error.code === 403) {
           alert('Permission denied');
       } else if (error.code === 400) {
           alert('Invalid data: ' + error.message);
       }
   }


Error Codes
-----------

+------+--------------------------------------------------+
| Code | Description                                      |
+======+==================================================+
| 400  | Bad request (invalid data, missing fields,       |
|      | instance not found, or instance not in the       |
|      | channel's anchor context)                        |
+------+--------------------------------------------------+
| 403  | Forbidden (type/operation not declared in        |
|      | ``Meta.writable``, or denied by ``can_*``        |
|      | method)                                          |
+------+--------------------------------------------------+
| 500  | Server error                                     |
+------+--------------------------------------------------+


WebSocket Protocol
==================

Save Operation
--------------

Request:

.. code-block:: json

   {
       "type": "write",
       "writeId": 123,
       "operation": "save",
       "instanceType": "myapp.serializers.TaskSerializer",
       "instanceId": 42,
       "data": {"name": "Updated Name"}
   }

Success response:

.. code-block:: json

   {"type": "writeResponse", "writeId": 123, "success": true}

Error response:

.. code-block:: json

   {
       "type": "writeResponse",
       "writeId": 123,
       "success": false,
       "error": {"code": 403, "message": "Permission denied"}
   }


Create Operation
----------------

Request:

.. code-block:: json

   {
       "type": "write",
       "writeId": 124,
       "operation": "create",
       "instanceType": "myapp.serializers.TaskSerializer",
       "parentType": "myapp.serializers.JobSerializer",
       "parentId": 1,
       "relationName": "tasks",
       "data": {"name": "New Task", "developer": 5}
   }


Delete Operation
----------------

Request:

.. code-block:: json

   {
       "type": "write",
       "writeId": 125,
       "operation": "delete",
       "instanceType": "myapp.serializers.TaskSerializer",
       "instanceId": 42
   }


Best Practices
==============

1. **Declare only what you need in ``Meta.writable``**: Only list the
   serializer types and operations that the frontend genuinely needs.
   Undeclared types are completely blocked on the server, providing
   defense in depth independent of ``can_*`` logic.

2. **Always implement authorization**: The ``can_*`` defaults are
   deny-all. Explicitly override them for each operation you allow.
   ``Meta.writable`` controls *what* is writable; ``can_*`` controls
   *who* may write.

3. **Check specific fields in can_save**: Only allow updates to fields
   the user should be able to modify.

4. **Use transactions**: The server-side operations are atomic. If
   authorization fails, no database changes occur.

5. **Handle errors gracefully**: Catch errors and show appropriate
   feedback to users.

6. **Don't rely on optimistic state for subsequent operations**: Wait
   for the server broadcast before assuming data is persisted.


Example: Complete Channel
=========================

.. code-block:: python

   from rxdjango.channels import ContextChannel
   from rxdjango.actions import action
   from rxdjango.operations import SAVE, CREATE, DELETE
   from myapp.serializers import JobNestedSerializer, TaskSerializer
   from myapp.models import Task

   class JobContextChannel(ContextChannel):
       class Meta:
           state = JobNestedSerializer()
           writable = {
               TaskSerializer: [SAVE, CREATE, DELETE],
           }

       @staticmethod
       def has_permission(user, **kwargs):
           return user.is_authenticated

       def can_save(self, instance, data):
           # Allow updating task status for assigned users
           if isinstance(instance, Task):
               if set(data.keys()) <= {'status', 'notes'}:
                   return instance.assignee_id == self.user.id
           return False

       def can_create(self, model_class, parent, data):
           # Allow project members to create tasks
           if model_class == Task:
               return self._is_project_member(parent.project_id)
           return False

       def can_delete(self, instance):
           # Allow task creators or admins to delete
           if isinstance(instance, Task):
               return (
                   instance.created_by_id == self.user.id or
                   self.user.is_admin
               )
           return False

       def _is_project_member(self, project_id):
           # Helper to check project membership
           from myapp.models import ProjectMember
           return ProjectMember.objects.filter(
               project_id=project_id,
               user_id=self.user.id
           ).exists()
