from app.services import ground_tool_result_answer, _is_weak_bot_answer


def test_weak_generic_phrase():
    assert _is_weak_bot_answer(
        "I'm here to help! Could you please provide more details about what you're looking for?"
    )


def test_ground_categories_filter_acts():
    node_result = {
        "success": True,
        "status_code": 200,
        "body": [
            {"id": "1", "slug": "act's", "name": "Act's", "level": 1},
            {
                "id": "2",
                "slug": "stamp_act",
                "name": "Stamp Act",
                "level": 2,
                "parent_id": "1",
            },
            {
                "id": "3",
                "slug": "registration_act",
                "name": "Registration Act",
                "level": 2,
                "parent_id": "1",
            },
            {"id": "4", "slug": "deed's", "name": "Deed's", "level": 1},
        ],
    }
    answer = ground_tool_result_answer(
        "aidoc_categories",
        node_result,
        "list me the categories about acts or any",
    )
    assert "Act" in answer
    assert "I'm here to help" not in answer
    assert "•" in answer


def test_ground_categories_all():
    node_result = {
        "success": True,
        "body": [
            {"name": "Act's", "slug": "act's", "level": 1},
            {"name": "Deed's", "slug": "deed's", "level": 1},
        ],
    }
    answer = ground_tool_result_answer(
        "aidoc_categories", node_result, "list categories"
    )
    assert "Act's" in answer
    assert "Deed's" in answer
