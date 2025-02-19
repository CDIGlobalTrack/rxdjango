
:: _context-channel:

========================
The ContextChannel class
========================

The `rxdjango.channels.ContextChannel` is the core of the RxDjango. Every
ContextChannel subclass must declare a `Meta` class containing a `state` property

   ```python
   from rxdjango.channels import ContextChannel
   from myapp.serializers import MyNestedSerializer
   
   class MyContextChannel(ContextChannel):

       class Meta:
           state = MyNestedSerializer()
   ```

A ContextChannel has to be either a single or a multiple instance channel. To
declare a channel that has several instances, `many=True` has to be passed to
the serializer:

   ```python
   from rxdjango.channels import ContextChannel
   from myapp.serializers import MyNestedSerializer
   
   class MyContextChannel(ContextChannel):

       class Meta:
           state = MyNestedSerializer()

       @staticmethod
       def has_permission(user, **kwargs)

       def get_instance_id(self, **kwargs):
           # this is optional, by default returns the first kwarg
	   return kwargs['my_model_id']
   ```

If the channel is a single instance channel, it must define `has_permission`
static method. Channel with many instances must define `list_instances`:

   ```python
   from rxdjango.channels import ContextChannel
   from myapp.serializers import MyNestedSerializer
   from mayapp.models import MyModel()
   
   class MyContextListChannel(ContextChannel):

       class Meta:
           state = MyNestedSerializer(many=True)


       async def list_instances(self, **kwargs):
           # Return a queryset of visible objects
	   # You may need to use @database_sync_to_async
   ```

In both cases, `**kwargs` comes from the paths registered in asgi.py:

   ```python
   from myapp.channels import MyContextChannel
   
   websocket_urlpatterns = [
       path('ws/myapp/instance/<str:mymodel_id>/', MyContextChannel.as_asgi()),
       path('ws/myapp/list', MyContextListChannel.as_asgi()),
   ]

   application = ProtocolTypeRouter({
       "http": app,
       "websocket": URLRouter(
           websocket_urlpatterns
       ),
   })
   ```

Actions
-------
12345678901234567890123456789012345678901234567890123456789012345678901234567890

By the use of actions, methods can be registered to be called directly from
the frontend.

   ```python
   from rxdjango.actions import action
   
   class MyContextListChannel(ContextChannel):

       ...

       @action
       async def change_instance_state(self, some_var: int) -> bool:
           # do something, changes in state will automatically be broadcast
	   return result
   ```

When creating actions, it's important to use typehints, so the typings can
automatically be generated for the frontend.

In channels with a list of instances, actions can be used to change the
instances in the context, for example to create a search:

   ```python
   from rxdjango.actions import action

   class MyContextListChannel(ContextChannel):

       search_term = None
       
       @action
       async def search(self, term):
           self.search_term = term
	   instances = self._list_instances()
	   self.clear()
	   for instance in instances:
	       self.add_instance(instance)
   ```

`add_instance`, `remove_instance` and `clear` methods can be used to change
the instances in the context, for list channels. 






