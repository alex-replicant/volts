#!/bin/bash

RESULT_FILE=${RESULT_FILE-"result.json"}
PORT=${PORT="5060"}

echo " >--- Running scenario ${XML_CONF}"

/git/voip_patrol/voip_patrol --port ${PORT} --conf /xml/${XML_CONF}.xml --output /output/${RESULT_FILE} --log-level-file ${LOG_LEVEL_FILE} --log-level-console ${LOG_LEVEL}
VP_EXIT_CODE=$?

echo " ---> Scenario ${XML_CONF} done (exit code ${VP_EXIT_CODE})"

# Persist the voip_patrol process exit code as a JSONL marker so the report can
# fail the scenario on a non-zero exit (crash, bad config, ...) even when the
# result file itself looks complete. Handled explicitly by report.py.
printf '{"vp_exit": {"name": "%s", "code": %d}}\n' "${XML_CONF}" "${VP_EXIT_CODE}" >> /output/${RESULT_FILE}

chmod 777 /output
chmod 666 /output/${RESULT_FILE}
