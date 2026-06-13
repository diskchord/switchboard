package com.example.texting;

import android.app.job.JobInfo;
import android.app.job.JobParameters;
import android.app.job.JobScheduler;
import android.app.job.JobService;
import android.content.ComponentName;
import android.content.Context;

public class NotificationPollService extends JobService {
    static final String ACTION_OPEN_CONVERSATION = "com.example.texting.OPEN_CONVERSATION";
    static final String EXTRA_CONVERSATION_ID = "conversation_id";

    private static final int JOB_ID = 2046;
    static void schedule(Context context) {
        JobScheduler scheduler = (JobScheduler) context.getApplicationContext().getSystemService(Context.JOB_SCHEDULER_SERVICE);
        if (scheduler == null) {
            return;
        }
        ComponentName component = new ComponentName(context.getApplicationContext(), NotificationPollService.class);
        JobInfo job = new JobInfo.Builder(JOB_ID, component)
            .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
            .setPersisted(true)
            .setPeriodic(MobileNotificationClient.getPollIntervalMillis(context))
            .build();
        scheduler.schedule(job);
    }

    static void pollNow(Context context) {
        Context appContext = context.getApplicationContext();
        new Thread(() -> MobileNotificationClient.poll(appContext), "text-notification-poll-now").start();
    }

    @Override
    public boolean onStartJob(JobParameters params) {
        new Thread(() -> {
            MobileNotificationClient.poll(getApplicationContext());
            jobFinished(params, false);
        }, "text-notification-poll").start();
        return true;
    }

    @Override
    public boolean onStopJob(JobParameters params) {
        return true;
    }
}
