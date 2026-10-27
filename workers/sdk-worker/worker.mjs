import { readFileSync } from "node:fs";
import { compileAixProfile, AIX_COMPILATION_PROFILE_VERSION, AIX_PUBLIC_CONTRACT_REVISION } from "@hyeve/agent-sdk/integrations/aix";
import { SDK_PUBLIC_API_VERSION } from "@hyeve/agent-sdk";
const input=JSON.parse(readFileSync(0,"utf8"));
if(input.mode!=="credential_free_fake") throw new Error("SDK_WORKER_MODE_DENIED");
if(input.profile.contractVersion!==AIX_COMPILATION_PROFILE_VERSION||input.profile.aixSourceRevision!==AIX_PUBLIC_CONTRACT_REVISION||input.profile.sdkApiVersion!==SDK_PUBLIC_API_VERSION) throw new Error("SDK_WORKER_CONTRACT_MISMATCH");
const resolved=compileAixProfile(input.profile);
process.stdout.write(JSON.stringify({contractVersion:"hyeve.aix/sdk-lifecycle/v1",eventId:input.eventId,tenantId:input.tenantId,projectId:input.projectId,sessionId:input.sessionId,taskId:resolved.taskId,attemptId:resolved.attemptId,sequence:input.sequence,kind:"attempt.result",payload:{status:"SUCCEEDED",specDigest:resolved.specDigest,providerRequest:false,credentialUsed:false,sourceRevision:resolved.repository.sourceRevision,sdkPackageVersion:"0.9.0-pilot.2",runtimeVersion:resolved.adapter.version}}));
