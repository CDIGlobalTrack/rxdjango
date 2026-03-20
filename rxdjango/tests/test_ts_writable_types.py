from collections import defaultdict
from types import SimpleNamespace

from rest_framework import serializers

from rxdjango.operations import CREATE
from rxdjango.ts.channels import _build_writable_types


def _make_node(serializer_instance, instance_type, *, many=False, children=None):
    return SimpleNamespace(
        nested_serializer=serializer_instance,
        instance_type=instance_type,
        many=many,
        children=children or {},
    )


class RunCascadeSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)


class TriggerSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)


class TriggerSeenSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    trigger = serializers.IntegerField(required=False)
    user = serializers.IntegerField(required=False)


def test_build_writable_types_for_deep_create_only_child():
    run_node = _make_node(
        RunCascadeSerializer(),
        'inspection.serializers.RunCascadeSerializer',
    )
    trigger_node = _make_node(
        TriggerSerializer(),
        'detection.serializers.TriggerSerializer',
        many=True,
    )
    seen_node = _make_node(
        TriggerSeenSerializer(),
        'detection.serializers.TriggerSeenSerializer',
        many=True,
    )

    run_node.children = {'trigger_set': trigger_node}
    trigger_node.children = {'triggerseen_set': seen_node}

    state_model = run_node
    state_model.index = {
        run_node.instance_type: [run_node],
        trigger_node.instance_type: [trigger_node],
        seen_node.instance_type: [seen_node],
    }

    type_lines, state_type_name, helper_imports = _build_writable_types(
        state_model,
        {seen_node.instance_type: [CREATE]},
        defaultdict(list),
    )

    rendered = '\n'.join(type_lines)

    assert state_type_name == 'RunCascadeState'
    assert helper_imports == {'Creatable'}
    assert 'Saveable<' not in rendered
    assert 'Deleteable<' not in rendered
    assert 'type WritableTriggerSeen = TriggerSeenType;' in rendered
    assert 'type TriggerSeenPayload = {' in rendered
    assert 'type RunCascadeState = Omit<RunCascadeType,' in rendered
    assert "  'trigger_set'" in rendered
    assert '  trigger_set: (' in rendered
    assert "    Omit<TriggerType," in rendered
    assert "      'triggerseen_set'" in rendered
    assert '    > & {' in rendered
    assert '      triggerseen_set: Creatable<WritableTriggerSeen, TriggerSeenPayload>;' in rendered
    assert '    }' in rendered
    assert '  )[];' in rendered
