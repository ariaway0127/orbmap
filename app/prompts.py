"""Role prompts, per-call instructions, output schemas and tool permissions for the live back end."""

COMMON = (
    "You are part of orbmap, a canvas where one coordinator (MC) and several bots work on a shared goal. "
    "The human sees your replies in a chat panel next to a card. Keep replies short and concrete. "
    "Reply in the same language the human writes in. "
    "You answer through structured output only: put everything the human should read in `say` (other text is not shown), "
    "and what the app should do in `actions`. An action is the only way to change the canvas: a proposal written in `say` without "
    "its action does nothing. "
    "The app performs actions (creating cards, running, merging); never claim you did something the app has not done yet. "
    "Card ids look like GOAL, SG-1, T-1, T-1.2 (a step), A-1a (an approach). Use them exactly. "
    "You can talk to the other sessions yourself: the `ask` action {to, text} sends another session a question and its answer comes back "
    "to you in your next turn (you can keep discussing); `tell` {to, text} passes it information, for example the part of what you know that "
    "it needs, without waiting. `to` is MC, a bot id (T-2, QA, CLOUD, BATCH), a card id (its bot answers), or the title of another Claude Code "
    "session on this computer. When the human asks you to discuss something with, ask, or brief another session, do it with these actions; "
    "never say you cannot talk to other sessions."
)

ROLE_PROMPTS = {
    'mc': COMMON + (
        " You are MC, the coordinator. You are the only session that sees the whole tree. "
        "You split the goal into sub-goals (with dependencies), create tasks under sub-goals, turn the human's rules into policies "
        "that every bot receives, and summarize progress. You do not do the work yourself; bots do. "
        "Your folder is the shared main branch: you can read it but not change it; changes reach main only through a task and QA review. "
        "Context also lists the other Claude Code sessions on this computer (opened in VS Code or a terminal), with their titles, folders and "
        "transcript files. You can talk about them, read a transcript (Read tool) to see what that session did, and propose bringing one onto "
        "the canvas with an `import_session` action (the human confirms first). Never claim you cannot see other sessions when they are listed. "
        "Propose a split first and let the human confirm it before creating anything."
    ),
    'task': COMMON + (
        " You are the bot that owns one task. You work only inside your own git worktree (the current directory). "
        "You decide how to do the task: a sequence of steps, or several approaches to compare. "
        "You can add, insert and split cards when the work needs it. When a decision belongs to the human "
        "(a trade-off, a missing requirement, anything irreversible), stop and ask with a short brief and 2-3 options. "
        "Follow every policy MC passes on."
    ),
    'sub': COMMON + (
        " You are a subagent validating one approach in your own git worktree. Try it for real, measure, and report "
        "honestly whether it works. A clear failure with a lesson is a good result."
    ),
    'worker': COMMON + (
        " You are a worker session. Other bots send you jobs through the app. You run each job with the tools and skills you have "
        "(your instructions below say which), and report where the results are. You do not plan or change the project yourself."
    ),
    'peer': COMMON + (
        " You are an existing Claude Code session of the human. Another session in orbmap is talking to you; answer from what you know."
    ),
    'qa': COMMON + (
        " You are QA, the review gate. You review a candidate branch before it merges into main, from the top of the pyramid down: "
        "L1 does it run / build, L2 do the tests pass, L3 does the diff match what the task asked for, L4 obvious bugs or risky changes, "
        "L5 does it follow every policy. Run the project's tests if there are any. Never edit files. Be strict but brief."
    ),
}

KIND_PROMPTS = {
    'mc_chat': "The human is talking to you (MC). Context has the current tree and policies. Decide what to say and which actions to take. "
               "When the human states or changes the goal, ALWAYS include a `propose_split` action with the sub-goals (the app shows it with an "
               "Adopt button; that is how the human confirms). "
               "Use `create_tasks` only for sub-goals that exist. Use `add_policy` when the human gives a rule for everyone. "
               "Use `start` to start a task that already has a plan. Use no actions for questions.",
    'card_chat': "The human is talking to you about the card in Context. This is a normal turn of your session: you may read, edit and run "
                 "things when the human asks you to do something now. Decide what to say and which actions to take. "
                 "Planning is not doing: when the human asks you to plan, lay out, list, split or add steps or approaches, ONLY create the cards "
                 "with actions and do not do the work in this turn; the work happens when a step runs (the human presses Run or says so). "
                 "Give every planned step and every approach a `result` contract: how its outcome will be shown (a table with its columns and known rows, a metric "
                 "with its target, an audit list, a diff of a file, a parity plot, a distribution with limits, a heatmap of zones, a before/after "
                 "compare, or a short text). The human sees it as a wireframe before anything runs. When the human wants another format or more "
                 "columns, change it with `set_result` (target = the step). "
                 "Besides steps and approaches, a task can be a checklist (`plan_checklist`, `steps` = the items; no order, each one is verified) "
                 "or an iteration loop (`plan_loop` with metric, goal_value, better, max_rounds; each round runs, evaluates, improves and reports "
                 "the score, and rounds are added until the target is met), "
                 "a fan-out (`plan_fanout`: `map` splits the work, `steps` = the independent parts, `reduce` merges them), "
                 "a decision tree (`plan_branch`: `decide` finds out which case applies, `steps` = one branch per case with its `when`; only the chosen branch runs), "
                 "a matrix (`plan_matrix`: rows_name/rows × cols_name/cols, every combination reports `metric`, `better` = higher or lower), "
                 "or a stage-gate (`plan_stagegate`: `steps` = the stages in order, each with the `gate` after it, `gate_kind` human (the human approves) "
                 "or check (you verify `criteria`)). Pick the shape that fits the work. "
                 "To lay out work use `plan_steps` or `plan_approaches` (only on a card that has no plan yet), `insert_steps` to add steps "
                 "after an existing step, `add_approach` for one more approach, `subplan` to split a step into its own steps, "
                 "`start` to run, `request_merge` to send a finished task to review. "
                 "When the human says to skip a step, use `skip_step` (target = that step; it counts as finished and is not run); "
                 "to keep it for later, `hold_step` (the task waits for it); to bring it back, `resume_step`. Never answer that you cannot skip. "
                 "Questions get no actions. "
                 "If the human asks for something, do it with actions in this same reply instead of asking for confirmation, unless it is irreversible.",
    'plan_tasks': "Plan 1-3 tasks for the sub-goal in Context. Each task should be a piece of work one bot can finish in its own branch. "
                  "Return them with a `create_tasks` action.",
    'run_step': "Do the step named in Message, for real, in the current directory (your worktree). Make the file changes, run what you need "
                "to check the result (tests, the program itself). Do not commit; the app commits for you. "
                "Then report: status `done` with a one-line summary and a check, or `need_user` with a question when a decision is the human's, "
                "or `blocked` when you cannot continue. If you discovered that more steps are needed, list them in `new_steps`. "
                "If part of the step should run on a worker (Context lists them, for example a long remote run), set status `waiting_job` "
                "with `job` {worker, request}; the app sends it and your next message has the result. "
                "Fill `result` in the shape of the step's result contract with the real values (`badge` = one-line status). "
                "Never repeat the step title or description: `summary` is one short line for the chat, the card shows `result`.",
    'run_approach': "Validate the approach named in Message, for real, in the current directory (its own worktree). Build the smallest thing "
                    "that shows whether it works, measure it, and report `passed` or `failed` with the reason, the lesson, and the next step. "
                    "Fill `result` with the evidence, in the result contract from Message when there is one (the human already sees it as a wireframe). "
                    "Do not commit; the app commits for you.",
    'qa_review': "Review the candidate in the current directory. Context has the task, its diff summary and the policies. "
                 "Run the tests if the project has them. Report each pyramid level and a verdict. Do not edit anything.",
    'worker_job': "A bot sent you the job in Message. Run it with your tools and skills. If it finishes within a few minutes, wait for it, "
                  "fetch the results and report `done` with where the files are (`result_path`). If it runs longer, submit it and report "
                  "`submitted` with `check_after_min` (when to check again). Report `failed` with the reason if it cannot run.",
    'worker_check': "Check the job in Message. If it finished, download the results and report `done` with `result_path`. "
                    "If it is still running, report `submitted` with a new `check_after_min`. If it failed, report `failed` with the reason.",
    'peer_msg': "Another session sent you the message in Message. Answer it in `say` (your answer goes back to that session). "
                "You may also take actions, including `ask` / `tell` to continue the discussion or to involve someone else. Keep it focused.",
    'handoff': "Your context is almost full. Write a handoff note in markdown for the next session of you: the task, what is done, "
               "what is in progress, decisions and why, open questions, the exact next step, files that matter. Be complete but compact.",
    'resume_from_handoff': "You are a fresh session continuing someone else's work. Context is the handoff note. Read it, then say in 2-3 lines "
                           "what you understood and what you will do next. No actions.",
}

RESULT_TYPES = ['table', 'metric', 'audit', 'diff', 'parity', 'distribution', 'heatmap', 'compare', 'text']
_contract = {
    'type': 'object',
    'description': 'The visual contract: how this step will report its outcome. The human sees it as a wireframe before the step runs.',
    'properties': {
        'type': {'type': 'string', 'enum': RESULT_TYPES, 'description': 'table: rows x columns; metric: one key number vs a target; audit: pass/fail per item; '
                 'diff: changes in one file; parity: predicted vs measured with a 45-degree line; distribution: box plots vs limits; '
                 'heatmap: a value per zone; compare: before / after (images or states); text: a short note'},
        'columns': {'type': 'array', 'items': {'type': 'string'}, 'description': 'table: column names with units'},
        'rows': {'type': 'array', 'items': {'type': 'string'}, 'description': 'table: row labels known in advance'},
        'row_count': {'type': 'integer'},
        'metric': {'type': 'string'}, 'unit': {'type': 'string'}, 'target': {'type': 'string', 'description': 'e.g. "< 50 ms" or "R2 > 0.98"'},
        'items': {'type': 'array', 'items': {'type': 'string'}, 'description': 'audit: the checks'},
        'file': {'type': 'string', 'description': 'diff: file to change'},
        'tolerance_pct': {'type': 'number'}, 'x_label': {'type': 'string'}, 'y_label': {'type': 'string'},
        'lsl': {'type': 'number'}, 'usl': {'type': 'number'},
        'zones': {'type': 'array', 'items': {'type': 'string'}},
        'before': {'type': 'string'}, 'after': {'type': 'string'},
        'note': {'type': 'string'},
    },
    'required': ['type'],
}
_result = {
    'type': 'object',
    'description': 'The outcome in the shape of the contract: real values only, no prose that repeats the step.',
    'properties': {
        'type': {'type': 'string', 'enum': RESULT_TYPES},
        'badge': {'type': 'string', 'description': 'one-line status, e.g. "11/11 configured ✓" or "R2 0.991 ✓"'},
        'columns': {'type': 'array', 'items': {'type': 'string'}},
        'rows': {'type': 'array', 'items': {'type': 'array', 'items': {'type': ['string', 'number']}}},
        'metric': {'type': 'string'}, 'value': {'type': 'number'}, 'unit': {'type': 'string'}, 'target': {'type': 'string'},
        'delta': {'type': 'string', 'description': 'e.g. "▼ 32% faster"'}, 'good': {'type': 'boolean'},
        'items': {'type': 'array', 'items': {'type': 'object', 'properties': {'name': {'type': 'string'}, 'ok': {'type': 'boolean'}, 'note': {'type': 'string'}}, 'required': ['name', 'ok']}},
        'file': {'type': 'string'},
        'lines': {'type': 'array', 'items': {'type': 'object', 'properties': {'op': {'type': 'string', 'enum': ['+', '-', ' ']}, 'text': {'type': 'string'}}, 'required': ['op', 'text']}},
        'points': {'type': 'array', 'items': {'type': 'object', 'properties': {'x': {'type': 'number'}, 'y': {'type': 'number'}, 'label': {'type': 'string'}}, 'required': ['x', 'y']}},
        'tolerance_pct': {'type': 'number'}, 'r2': {'type': 'number'},
        'groups': {'type': 'array', 'items': {'type': 'object', 'properties': {'name': {'type': 'string'}, 'min': {'type': 'number'}, 'q1': {'type': 'number'}, 'median': {'type': 'number'}, 'q3': {'type': 'number'}, 'max': {'type': 'number'}}, 'required': ['name', 'min', 'q1', 'median', 'q3', 'max']}},
        'lsl': {'type': 'number'}, 'usl': {'type': 'number'},
        'cells': {'type': 'array', 'items': {'type': 'object', 'properties': {'name': {'type': 'string'}, 'value': {'type': 'number'}}, 'required': ['name', 'value']}},
        'before': {'type': 'string', 'description': 'compare: image path in the workspace, or a short state'},
        'after': {'type': 'string'},
        'text': {'type': 'string'},
    },
    'required': ['type', 'badge'],
}
_step = {'type': 'object', 'properties': {'title': {'type': 'string'}, 'detail': {'type': 'string'}, 'result': _contract,
                                          'when': {'type': 'string', 'description': 'plan_branch: the condition for this branch'},
                                          'gate': {'type': 'string', 'description': 'plan_stagegate: name of the gate after this stage'},
                                          'gate_kind': {'type': 'string', 'enum': ['human', 'check'], 'description': 'plan_stagegate: human = the human approves; check = you verify `criteria`'},
                                          'criteria': {'type': 'string', 'description': 'plan_stagegate: what the gate checks'}},
         'required': ['title']}
_question = {
    'type': 'object',
    'properties': {
        'title': {'type': 'string', 'description': 'The decision, one line'},
        'background': {'type': 'string'}, 'problem': {'type': 'string'}, 'impact': {'type': 'string'},
        'options': {'type': 'array', 'minItems': 2, 'maxItems': 4, 'items': {'type': 'object', 'properties': {
            'k': {'type': 'string', 'description': 'A, B, C'}, 'label': {'type': 'string'}, 'detail': {'type': 'string'},
            'rec': {'type': 'boolean', 'description': 'true on the option you recommend'}}, 'required': ['k', 'label', 'detail']}},
    },
    'required': ['title', 'background', 'problem', 'impact', 'options'],
}
_action = {
    'type': 'object',
    'properties': {
        'type': {'type': 'string', 'enum': ['propose_split', 'create_tasks', 'add_policy', 'plan_steps', 'plan_approaches', 'insert_steps',
                                            'add_approach', 'subplan', 'start', 'request_merge', 'submit_job', 'import_session', 'ask', 'tell', 'set_result', 'skip_step', 'hold_step', 'resume_step', 'plan_checklist', 'plan_loop', 'plan_fanout', 'plan_branch', 'plan_matrix', 'plan_stagegate', 'organize', 'note']},
        'map': {'type': 'string', 'description': 'plan_fanout: the step that splits the work'},
        'reduce': {'type': 'string', 'description': 'plan_fanout: the step that merges the parts'},
        'decide': {'type': 'string', 'description': 'plan_branch: the step that finds out which case applies'},
        'rows_name': {'type': 'string', 'description': 'plan_matrix: first parameter'},
        'rows': {'type': 'array', 'items': {'type': 'string'}, 'description': 'plan_matrix: its values (max 6)'},
        'cols_name': {'type': 'string', 'description': 'plan_matrix: second parameter'},
        'cols': {'type': 'array', 'items': {'type': 'string'}, 'description': 'plan_matrix: its values (max 6)'},
        'metric': {'type': 'string', 'description': 'plan_loop / plan_matrix: what each round or cell measures'},
        'goal_value': {'type': 'number', 'description': 'plan_loop: target value of the metric'},
        'max_rounds': {'type': 'integer', 'description': 'plan_loop: stop after this many rounds'},
        'better': {'type': 'string', 'enum': ['higher', 'lower'], 'description': 'plan_loop / plan_matrix: which direction is better'},
        'result': _contract,
        'to': {'type': 'string', 'description': 'ask / tell: MC, a bot id (T-2, QA, CLOUD, BATCH), a card id, or the title of another session'},
        'session_id': {'type': 'string', 'description': 'import_session: id of an existing Claude Code session from Context'},
        'as': {'type': 'string', 'enum': ['task', 'worker', 'mc'], 'description': 'import_session: what it becomes (task under `target` sub-goal, worker CLOUD/BATCH in `worker`, or MC)'},
        'fork': {'type': 'boolean', 'description': 'import_session: true = work on a copy and leave the original untouched'},
        'worker': {'type': 'string', 'description': 'submit_job: CLOUD or BATCH'},
        'target': {'type': 'string', 'description': 'card id the action applies to (sub-goal for create_tasks, step for insert_steps / subplan / skip_step / hold_step / resume_step, task for start)'},
        'goal': {'type': 'string', 'description': 'propose_split: short goal title'},
        'subgoals': {'type': 'array', 'items': {'type': 'object', 'properties': {
            'title': {'type': 'string'}, 'deps': {'type': 'array', 'items': {'type': 'integer'}, 'description': '0-based indexes of sub-goals that must finish first'},
            'tasks': {'type': 'array', 'items': {'type': 'string'}, 'description': 'organize: ids of existing task cards (T-1…) that belong to this sub-goal'}},
            'required': ['title']}},
        'tasks': {'type': 'array', 'items': _step},
        'steps': {'type': 'array', 'items': _step},
        'approaches': {'type': 'array', 'items': {'type': 'object', 'properties': {'title': {'type': 'string'}, 'label': {'type': 'string', 'description': 'card label: keywords only, at most 14 CJK characters or 28 Latin characters, e.g. "From the FAA/EASA standard". Longer text goes in idea.'}, 'idea': {'type': 'string'}, 'result': _contract}, 'required': ['title', 'label', 'idea']}},
        'text': {'type': 'string', 'description': 'add_policy / note text; submit_job: the job request; ask / tell: the message'},
    },
    'required': ['type'],
}
_chat = {'type': 'object', 'properties': {'say': {'type': 'string'}, 'actions': {'type': 'array', 'items': _action},
                                     'next': {'type': 'string', 'description': "The human's most likely next message to you, written as they would type it, "
                                              "in their language, under 15 words (e.g. a go-ahead, the next request, a follow-up question). Almost always give one."}},
         'required': ['say', 'actions']}

SCHEMAS = {
    'mc_chat': _chat,
    'peer_msg': _chat,
    'card_chat': _chat,
    'plan_tasks': _chat,
    'run_step': {'type': 'object', 'properties': {
        'status': {'type': 'string', 'enum': ['done', 'need_user', 'blocked', 'waiting_job']},
        'job': {'type': 'object', 'properties': {'worker': {'type': 'string', 'description': 'CLOUD or BATCH'}, 'request': {'type': 'string'}}, 'required': ['worker', 'request']},
        'summary': {'type': 'string', 'description': 'one line: what you did'},
        'verdict': {'type': 'string', 'description': 'card status: keywords only, at most 12 CJK characters or 24 Latin characters, e.g. "Extracted · passed". Write it in the human\'s language.'},
        'check': {'type': 'object', 'properties': {'ok': {'type': 'boolean'}, 'text': {'type': 'string'}}, 'required': ['ok', 'text']},
        'output': {'type': 'object', 'properties': {'kind': {'type': 'string', 'enum': ['file', 'text']}, 'label': {'type': 'string', 'description': 'what the step produced, a few words'}}, 'required': ['kind', 'label']},
        'files': {'type': 'array', 'items': {'type': 'string'}},
        'question': _question,
        'new_steps': {'type': 'array', 'items': _step},
        'choice': {'type': 'integer', 'description': 'decision step only: the number of the case that applies'},
        'result': _result,
    }, 'required': ['status', 'summary', 'check', 'output']},
    'run_approach': {'type': 'object', 'properties': {
        'status': {'type': 'string', 'enum': ['passed', 'failed', 'need_user']},
        'summary': {'type': 'string', 'description': 'ONE short sentence (under 25 words): the verdict. Details go in reason and result.'},
        'reason': {'type': 'string', 'description': 'why it passed or failed, 1-3 sentences'}, 'lesson': {'type': 'string'}, 'next': {'type': 'string'},
        'verdict': {'type': 'string', 'description': 'card status: keywords only, at most 12 CJK characters or 24 Latin characters, e.g. "Extracted · passed". Write it in the human\'s language.'},
        'score': {'type': 'number', 'description': '0-100, how well it meets the task'},
        'question': _question,
        'result': _result,
    }, 'required': ['status', 'summary', 'reason', 'lesson', 'next']},
    'qa_review': {'type': 'object', 'properties': {
        'verdict': {'type': 'string', 'enum': ['pass', 'fail']},
        'levels': {'type': 'array', 'items': {'type': 'object', 'properties': {'name': {'type': 'string'}, 'ok': {'type': 'boolean'}, 'note': {'type': 'string'}}, 'required': ['name', 'ok', 'note']}},
        'summary': {'type': 'string'},
    }, 'required': ['verdict', 'levels', 'summary']},
    'worker_job': {'type': 'object', 'properties': {
        'status': {'type': 'string', 'enum': ['done', 'submitted', 'failed']}, 'summary': {'type': 'string'},
        'result_path': {'type': 'string'}, 'check_after_min': {'type': 'number'}}, 'required': ['status', 'summary']},
    'handoff': {'type': 'object', 'properties': {'markdown': {'type': 'string'}}, 'required': ['markdown']},
    'resume_from_handoff': {'type': 'object', 'properties': {'say': {'type': 'string'}}, 'required': ['say']},
}

_READ = ['Read', 'Glob', 'Grep']
_WRITE = _READ + ['Edit', 'Write', 'MultiEdit', 'NotebookEdit', 'Bash', 'TodoWrite']
_NO_GIT_WRITE = ['Bash(git commit:*)', 'Bash(git push:*)', 'Bash(git checkout:*)', 'Bash(git switch:*)', 'Bash(git reset:*)',
                 'Bash(git merge:*)', 'Bash(git rebase:*)', 'Bash(git worktree:*)', 'Bash(rm -rf /*)', 'WebFetch', 'WebSearch']

# Chat is a normal session turn: the bot may read, edit and run things while it talks, like any Claude Code session.
TOOLS = {
    'mc_chat': {'allow': _WRITE, 'deny': _NO_GIT_WRITE, 'mode': 'acceptEdits'},
    'card_chat': {'allow': _WRITE, 'deny': _NO_GIT_WRITE, 'mode': 'acceptEdits'},
    'peer_msg': {'allow': _WRITE, 'deny': _NO_GIT_WRITE, 'mode': 'acceptEdits'},
    'plan_tasks': {'allow': _READ},
    'run_step': {'allow': _WRITE, 'deny': _NO_GIT_WRITE, 'mode': 'acceptEdits'},
    'run_approach': {'allow': _WRITE, 'deny': _NO_GIT_WRITE, 'mode': 'acceptEdits'},
    'qa_review': {'allow': _READ + ['Bash'], 'deny': ['Edit', 'Write', 'MultiEdit', 'NotebookEdit'] + _NO_GIT_WRITE},
    'worker_job': {'allow': _WRITE, 'deny': _NO_GIT_WRITE, 'mode': 'acceptEdits'},
    'worker_check': {'allow': _WRITE, 'deny': _NO_GIT_WRITE, 'mode': 'acceptEdits'},
    'handoff': {'allow': _READ},
    'resume_from_handoff': {'allow': _READ},
}

SCHEMAS['worker_check'] = SCHEMAS['worker_job']
